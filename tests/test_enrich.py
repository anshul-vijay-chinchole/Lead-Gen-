"""Tests for the contact finders: apollo, hunter, pattern, csv."""
from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest

from leadgen import registry
from leadgen.context import MissingCredentialError
from leadgen.enrich.apollo import ApolloFinder, apollo_email_status, clean_email
from leadgen.enrich.csv_finder import CsvFinder, map_status
from leadgen.enrich.hunter import (HunterFinder, executive_departments, hunter_departments, linkedin_url,
                                   verification_status)
from leadgen.enrich.pattern import (PatternFinder, contact_name_parts, name_token, normalize_pattern,
                                    render_pattern, split_full_name)
from leadgen.http import HttpError
from leadgen.models import Company, Contact, EmailStatus

APOLLO_SEARCH = "https://api.apollo.io/api/v1/mixed_people/api_search"
APOLLO_MATCH = "https://api.apollo.io/api/v1/people/match"
HUNTER_DOMAIN = "https://api.hunter.io/v2/domain-search"
HUNTER_FINDER = "https://api.hunter.io/v2/email-finder"

BUYERS = {"titles": ["CFO", "VP Finance", "Finance Director", "Controller"],
          "seniorities": ["c_suite", "vp", "director"],
          "departments": ["Finance", "Human Resources"]}


def acme(**kw) -> Company:
    base = dict(name="Acme Inc", domain="acme.com", location="Austin, TX", employees=120)
    base.update(kw)
    return Company(**base)


# --- Apollo payloads (documented shapes) -----------------------------------------------------

def apollo_person(pid, first, last, title, email=None, email_status=None, **extra):
    p = {
        "id": pid, "first_name": first, "last_name": last, "name": f"{first} {last}",
        "linkedin_url": f"http://www.linkedin.com/in/{first.lower()}-{last.lower()}",
        "title": title, "email_status": email_status, "photo_url": None, "twitter_url": None,
        "headline": title, "email": email, "organization_id": "org_acme",
        "state": "Texas", "city": "Austin", "country": "United States",
        "departments": ["master_finance"], "subdepartments": ["accounting"], "seniority": "c_suite",
        "organization": {"id": "org_acme", "name": "Acme", "primary_domain": "acme.com"},
    }
    p.update(extra)
    return p


def apollo_search_payload(*people, contacts=None):
    return {"breadcrumbs": [], "partial_results_only": False, "disable_eu_prospecting": False,
            "pagination": {"page": 1, "per_page": 10, "total_entries": len(people), "total_pages": 1},
            "contacts": contacts or [], "people": list(people)}


def apollo_match_payload(pid, first, last, title, email, status="verified"):
    return {"person": apollo_person(pid, first, last, title, email=email, email_status=status)}


# === Apollo =====================================================================================

def test_apollo_search_request_shape(make_ctx):
    ctx = make_ctx(env={"APOLLO_API_KEY": "ap-key"}, buyers=BUYERS)
    ctx.http.add("POST", APOLLO_SEARCH, json=apollo_search_payload())
    assert ApolloFinder({}, ctx).find(acme()) == []
    call = ctx.http.calls[0]
    assert call["method"] == "POST" and call["url"] == APOLLO_SEARCH
    assert call["headers"]["x-api-key"] == "ap-key"
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["json"] == {
        "page": 1, "per_page": 10, "q_organization_domains_list": ["acme.com"],
        "person_titles": ["CFO", "VP Finance", "Finance Director", "Controller"],
        "include_similar_titles": True, "person_seniorities": ["c_suite", "vp", "director"],
    }


def test_apollo_search_by_org_id_and_config_overrides(make_ctx):
    ctx = make_ctx(env={"APOLLO_API_KEY": "ap-key"}, buyers=BUYERS)
    url = "https://proxy.example/api/v1/mixed_people/search"
    ctx.http.add("POST", url, json=apollo_search_payload())
    finder = ApolloFinder({"base_url": "https://proxy.example/", "search_path": "/api/v1/mixed_people/search",
                           "per_page": 500, "titles": ["Owner"], "seniorities": "owner",
                           "include_similar_titles": False,
                           "filters": {"person_locations": ["Texas"]}}, ctx)
    finder.find(acme(data={"apollo_id": "org_123"}))
    body = ctx.http.calls[0]["json"]
    assert ctx.http.calls[0]["url"] == url
    assert body["organization_ids"] == ["org_123"] and "q_organization_domains_list" not in body
    assert body["per_page"] == 100  # clamped
    assert body["person_titles"] == ["Owner"] and body["person_seniorities"] == ["owner"]
    assert body["include_similar_titles"] is False and body["person_locations"] == ["Texas"]


def test_apollo_no_titles_no_seniorities(make_ctx):
    ctx = make_ctx(env={"APOLLO_API_KEY": "k"})
    ctx.http.add("POST", APOLLO_SEARCH, json=apollo_search_payload())
    ApolloFinder({"per_page": 5}, ctx).find(acme())
    body = ctx.http.calls[0]["json"]
    assert body == {"page": 1, "per_page": 5, "q_organization_domains_list": ["acme.com"]}


def test_apollo_skips_company_without_domain_or_id(make_ctx):
    ctx = make_ctx(env={"APOLLO_API_KEY": "k"})
    assert ApolloFinder({}, ctx).find(Company(name="No Domain Ltd")) == []
    assert ctx.http.calls == []


def test_apollo_maps_people(make_ctx):
    ctx = make_ctx(env={"APOLLO_API_KEY": "k"}, buyers=BUYERS)
    people = [
        apollo_person("p1", "Jane", "Doe", "Chief Financial Officer (CFO)", email="Jane.Doe@acme.com",
                      email_status="verified", phone_numbers=[{"raw_number": "+1 512 555 0100",
                                                               "sanitized_number": "+15125550100"}],
                      extrapolated_email_confidence=0.87),
        apollo_person("p2", "Bob", "Stone", "Controller", email="email_not_unlocked@domain.com",
                      email_status="verified"),
    ]
    ctx.http.add("POST", APOLLO_SEARCH, json=apollo_search_payload(*people))
    found = ApolloFinder({"reveal_emails": False}, ctx).find(acme())
    assert [c.full_name for c in found] == ["Jane Doe", "Bob Stone"]
    jane, bob = found
    assert jane.source == "apollo" and bob.source == "apollo"
    assert jane.email == "jane.doe@acme.com" and jane.email_status == EmailStatus.VALID
    assert jane.title == "Chief Financial Officer (CFO)"
    assert jane.linkedin_url == "http://www.linkedin.com/in/jane-doe"
    assert jane.phone == "+15125550100"
    assert jane.seniority == "c_suite" and jane.department == "finance"
    assert jane.location == "Austin, Texas, United States"
    assert jane.confidence == pytest.approx(0.87)
    assert jane.data["apollo_id"] == "p1" and jane.data["apollo_email_status"] == "verified"
    assert jane.data["apollo_organization_id"] == "org_acme"
    # placeholder address = no email
    assert bob.email == "" and bob.email_status == EmailStatus.UNKNOWN and bob.data["apollo_id"] == "p2"


@pytest.mark.parametrize("raw,expected", [
    ("verified", EmailStatus.VALID), ("likely to engage", EmailStatus.UNKNOWN),
    ("extrapolated", EmailStatus.UNKNOWN), ("guessed", EmailStatus.UNKNOWN),
    ("unavailable", EmailStatus.UNKNOWN), ("bounced", EmailStatus.INVALID), ("invalid", EmailStatus.INVALID),
    ("catch_all", EmailStatus.RISKY), ("accept_all", EmailStatus.RISKY), ("Verified", EmailStatus.VALID),
    (None, EmailStatus.UNKNOWN), ("something_else", EmailStatus.UNKNOWN),
])
def test_apollo_email_status_mapping(raw, expected):
    assert apollo_email_status(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("Jane@Acme.com", "jane@acme.com"), ("email_not_unlocked@domain.com", ""),
    ("email_not_unlocked@acme.com", ""), ("x@domain.com", ""), ("", ""), (None, ""), ("garbage", ""),
])
def test_apollo_clean_email(raw, expected):
    assert clean_email(raw) == expected


def test_apollo_new_endpoint_obfuscated_names_and_contacts_list(make_ctx):
    ctx = make_ctx(env={"APOLLO_API_KEY": "k"})
    new_style = {"id": "p9", "first_name": "Priya", "last_name_obfuscated": "Pa***l", "title": "CFO",
                 "last_refreshed_at": "2026-09-01T00:00:00.000+00:00", "has_email": True,
                 "has_city": True, "has_state": True, "has_country": True,
                 "organization": {"name": "Acme", "has_industry": True}}
    saved = apollo_person("c1", "Sam", "Lee", "VP Finance", email="sam@acme.com", email_status="verified",
                          person_id="p10")
    dup = dict(saved, id="c2")  # same person_id twice -> kept once
    ctx.http.add("POST", APOLLO_SEARCH, json=apollo_search_payload(new_style, contacts=[saved, dup]))
    found = ApolloFinder({"reveal_emails": False}, ctx).find(acme())
    assert len(found) == 2
    priya = next(c for c in found if c.first_name == "Priya")
    assert priya.last_name == "" and priya.full_name == "Priya Pa***l"
    assert priya.data["apollo_last_name_obfuscated"] == "Pa***l" and priya.data["apollo_has_email"] is True
    sam = next(c for c in found if c.first_name == "Sam")
    assert sam.data["apollo_id"] == "p10" and sam.email == "sam@acme.com"


def test_apollo_skips_malformed_people_and_rejects_bad_payload(make_ctx):
    ctx = make_ctx(env={"APOLLO_API_KEY": "k"})
    ctx.http.add("POST", APOLLO_SEARCH, json={"people": ["nope", None, {"id": "x"}, {"title": "CFO"},
                                                         {"id": "p1", "name": "Ann Smith"}]}, times=1)
    ctx.http.add("POST", APOLLO_SEARCH, json=[1, 2], times=1)
    finder = ApolloFinder({"reveal_emails": False}, ctx)
    found = finder.find(acme())
    assert [(c.first_name, c.last_name) for c in found] == [("Ann", "Smith")]
    with pytest.raises(ValueError, match="unexpected people-search response"):
        finder.find(acme())


def test_apollo_people_key_missing_is_empty(make_ctx):
    ctx = make_ctx(env={"APOLLO_API_KEY": "k"})
    ctx.http.add("POST", APOLLO_SEARCH, json={"pagination": {}})
    assert ApolloFinder({}, ctx).find(acme()) == []


def test_apollo_saves_org_id_hint_only(make_ctx):
    ctx = make_ctx(env={"APOLLO_API_KEY": "k"})
    ctx.http.add("POST", APOLLO_SEARCH, json=apollo_search_payload(
        apollo_person("p1", "Jane", "Doe", "CFO", email="jane@acme.com", email_status="verified")))
    company = acme(contacts=[Contact(full_name="Existing Person")], signals=[])
    before = copy.deepcopy(company)
    ApolloFinder({}, ctx).find(company)
    assert company.data == {"apollo_id": "org_acme"}
    company.data = {}
    assert company == before  # nothing else touched


def test_apollo_reveal_best_ranked_until_satisfied(make_ctx):
    ctx = make_ctx(env={"APOLLO_API_KEY": "k"}, buyers=BUYERS)
    people = [
        apollo_person("p1", "Ann", "Intern", "Finance Intern"),              # excluded title
        apollo_person("p2", "Carl", "Ctrl", "Controller"),                   # rank 3
        apollo_person("p3", "Fiona", "Dir", "Finance Director"),             # rank 2
        apollo_person("p4", "Vic", "Pres", "VP Finance", has_email=False),   # rank 1, Apollo has no email
    ]
    ctx.http.add("POST", APOLLO_SEARCH, json=apollo_search_payload(*people))
    ctx.http.add("POST", APOLLO_MATCH, fn=lambda call: apollo_match_payload(
        "p3", "Fiona", "Dir", "Finance Director", "fiona@acme.com"))
    found = ApolloFinder({}, ctx).find(acme())
    matches = ctx.http.calls_to("/people/match")
    assert len(matches) == 1  # the first reveal gave a verified email: stop (max_contacts_per_company=1)
    assert matches[0]["json"] == {"id": "p3", "reveal_personal_emails": False}
    assert matches[0]["headers"]["x-api-key"] == "k"
    fiona = next(c for c in found if c.first_name == "Fiona")
    assert fiona.email == "fiona@acme.com" and fiona.email_status == EmailStatus.VALID
    assert fiona.data["apollo_revealed"] is True and fiona.data["email_source"] == "apollo"
    assert len(found) == 4  # finders return everyone; selection happens later


def test_apollo_reveal_respects_limit_and_existing_emails(make_ctx):
    ctx = make_ctx(env={"APOLLO_API_KEY": "k"}, buyers=dict(BUYERS, max_contacts_per_company=3))
    people = [apollo_person(f"p{i}", f"N{i}", f"L{i}", "Controller") for i in range(5)]
    people.insert(0, apollo_person("p0x", "Cee", "Eff", "CFO", email="cee@acme.com", email_status="verified"))
    ctx.http.add("POST", APOLLO_SEARCH, json=apollo_search_payload(*people))
    ctx.http.add("POST", APOLLO_MATCH, json={"person": None})
    ApolloFinder({"reveal_limit": 2}, ctx).find(acme())
    assert [c["json"]["id"] for c in ctx.http.calls_to("/people/match")] == ["p0", "p1"]


def test_apollo_reveal_names_from_match_replace_obfuscated(make_ctx):
    ctx = make_ctx(env={"APOLLO_API_KEY": "k"})
    ctx.http.add("POST", APOLLO_SEARCH, json=apollo_search_payload(
        {"id": "p9", "first_name": "Priya", "last_name_obfuscated": "Pa***l", "title": "CFO", "has_email": True}))
    ctx.http.add("POST", APOLLO_MATCH, json=apollo_match_payload("p9", "Priya", "Patel", "CFO",
                                                                 "priya.patel@acme.com", "extrapolated"))
    (priya,) = ApolloFinder({}, ctx).find(acme())
    assert priya.full_name == "Priya Patel" and priya.last_name == "Patel"
    assert priya.email == "priya.patel@acme.com" and priya.email_status == EmailStatus.UNKNOWN
    assert "apollo_last_name_obfuscated" not in priya.data
    assert priya.linkedin_url and priya.location == "Austin, Texas, United States"


def test_apollo_reveal_require_title_match(make_ctx):
    ctx = make_ctx(env={"APOLLO_API_KEY": "k"}, buyers=BUYERS)
    ctx.http.add("POST", APOLLO_SEARCH, json=apollo_search_payload(
        apollo_person("p1", "Olga", "Other", "Head of Treasury")))
    ApolloFinder({"reveal_require_title_match": True}, ctx).find(acme())
    assert ctx.http.calls_to("/people/match") == []


@pytest.mark.parametrize("cfg", [{"reveal_emails": False}, {"reveal_limit": 0}])
def test_apollo_reveal_disabled(make_ctx, cfg):
    ctx = make_ctx(env={"APOLLO_API_KEY": "k"}, buyers=BUYERS)
    ctx.http.add("POST", APOLLO_SEARCH, json=apollo_search_payload(apollo_person("p1", "Jane", "Doe", "CFO")))
    ApolloFinder(cfg, ctx).find(acme())
    assert ctx.http.calls_to("/people/match") == []


def test_apollo_reveal_not_found_continues(make_ctx):
    ctx = make_ctx(env={"APOLLO_API_KEY": "k"}, buyers=BUYERS)
    ctx.http.add("POST", APOLLO_SEARCH, json=apollo_search_payload(
        apollo_person("p1", "Jane", "Doe", "CFO"), apollo_person("p2", "Vic", "Pres", "VP Finance")))
    ctx.http.add("POST", APOLLO_MATCH, status=422, json={"error": "not found"}, times=1)
    ctx.http.add("POST", APOLLO_MATCH, json=apollo_match_payload("p2", "Vic", "Pres", "VP Finance", "vic@acme.com"))
    found = ApolloFinder({}, ctx).find(acme())
    assert len(ctx.http.calls_to("/people/match")) == 2
    assert next(c for c in found if c.first_name == "Vic").email == "vic@acme.com"


def test_apollo_reveal_forbidden_keeps_people_and_blocks_complete(make_ctx):
    ctx = make_ctx(env={"APOLLO_API_KEY": "k"}, buyers=BUYERS)
    ctx.http.add("POST", APOLLO_SEARCH, json=apollo_search_payload(
        apollo_person("p1", "Jane", "Doe", "CFO"), apollo_person("p2", "Vic", "Pres", "VP Finance")))
    ctx.http.add("POST", APOLLO_MATCH, status=403, json={"error": "plan does not include enrichment"})
    finder = ApolloFinder({}, ctx)
    found = finder.find(acme())
    assert len(found) == 2 and len(ctx.http.calls_to("/people/match")) == 1
    vic = next(c for c in found if c.first_name == "Vic")
    assert finder.complete(acme(), vic) is vic and vic.email == ""
    assert len(ctx.http.calls_to("/people/match")) == 1  # blocked: no more credits/requests


def test_apollo_reveal_server_error_keeps_people(make_ctx):
    ctx = make_ctx(env={"APOLLO_API_KEY": "k"}, buyers=BUYERS)
    ctx.http.add("POST", APOLLO_SEARCH, json=apollo_search_payload(
        apollo_person("p1", "Jane", "Doe", "CFO"), apollo_person("p2", "Vic", "Pres", "VP Finance")))
    ctx.http.add("POST", APOLLO_MATCH, status=500, text="oops")
    found = ApolloFinder({}, ctx).find(acme())
    assert len(found) == 2 and len(ctx.http.calls_to("/people/match")) == 1


def test_apollo_search_http_error_propagates(make_ctx):
    ctx = make_ctx(env={"APOLLO_API_KEY": "bad"})
    ctx.http.add("POST", APOLLO_SEARCH, status=401, json={"error": "Invalid access credentials."})
    with pytest.raises(HttpError) as ei:
        ApolloFinder({}, ctx).find(acme())
    assert ei.value.status == 401


def test_apollo_missing_credentials_raise_lazily(make_ctx):
    ctx = make_ctx(env={})
    finder = ApolloFinder({}, ctx)  # constructing needs no key
    with pytest.raises(MissingCredentialError, match="APOLLO_API_KEY"):
        finder.find(acme())
    with pytest.raises(MissingCredentialError):
        finder.complete(acme(), Contact(first_name="Jane", last_name="Doe"))
    assert ctx.http.calls == []


def test_apollo_dry_run_makes_no_calls(make_ctx):
    ctx = make_ctx(env={"APOLLO_API_KEY": "k"}, dry_run=True)
    finder = ApolloFinder({}, ctx)
    assert finder.find(acme()) == []
    c = Contact(first_name="Jane", last_name="Doe")
    assert finder.complete(acme(), c) is c and c.email == ""
    assert ctx.http.calls == []


def test_apollo_complete_by_id(make_ctx):
    ctx = make_ctx(env={"APOLLO_API_KEY": "k"})
    ctx.http.add("POST", APOLLO_MATCH, json=apollo_match_payload("p1", "Jane", "Doe", "CFO", "jane@acme.com",
                                                                 "catch_all"))
    c = Contact(first_name="Jane", last_name="Doe", source="csv", data={"apollo_id": "p1"})
    out = ApolloFinder({}, ctx).complete(acme(), c)
    assert out is c and c.email == "jane@acme.com" and c.email_status == EmailStatus.RISKY
    assert c.source == "csv"  # the person's origin is not rewritten
    assert c.title == "CFO"  # missing fields filled
    assert ctx.http.calls[0]["json"] == {"id": "p1", "reveal_personal_emails": False}


def test_apollo_complete_by_name_and_domain(make_ctx):
    ctx = make_ctx(env={"APOLLO_API_KEY": "k"})
    ctx.http.add("POST", APOLLO_MATCH, json=apollo_match_payload("p1", "Jane", "Doe", "CFO", "jane@acme.com"))
    c = Contact(first_name="Jane", last_name="Doe", title="Finance Chief",
                linkedin_url="https://linkedin.com/in/janedoe")
    ApolloFinder({"reveal_personal_emails": True}, ctx).complete(acme(), c)
    assert ctx.http.calls[0]["json"] == {"reveal_personal_emails": True, "first_name": "Jane", "last_name": "Doe",
                                         "domain": "acme.com", "linkedin_url": "https://linkedin.com/in/janedoe"}
    assert c.email == "jane@acme.com" and c.email_status == EmailStatus.VALID
    assert c.title == "Finance Chief"  # existing values never overwritten
    assert c.data["apollo_id"] == "p1"


def test_apollo_complete_by_org_name_or_linkedin(make_ctx):
    ctx = make_ctx(env={"APOLLO_API_KEY": "k"})
    ctx.http.add("POST", APOLLO_MATCH, json={"person": None})
    finder = ApolloFinder({}, ctx)
    finder.complete(Company(name="Acme Inc"), Contact(first_name="Jane", last_name="Doe"))
    assert ctx.http.calls[-1]["json"]["organization_name"] == "Acme Inc"
    finder.complete(acme(), Contact(first_name="Jane", linkedin_url="https://linkedin.com/in/jd"))
    assert ctx.http.calls[-1]["json"] == {"reveal_personal_emails": False,
                                          "linkedin_url": "https://linkedin.com/in/jd"}


def test_apollo_complete_skips(make_ctx):
    ctx = make_ctx(env={"APOLLO_API_KEY": "k"})
    finder = ApolloFinder({}, ctx)
    has_email = Contact(first_name="Jane", last_name="Doe", email="jane@acme.com")
    assert finder.complete(acme(), has_email) is has_email
    no_data = Contact(first_name="Jane")
    assert finder.complete(acme(), no_data) is no_data
    tried = Contact(first_name="Jane", last_name="Doe", data={"apollo_id": "p1", "apollo_revealed": True})
    assert finder.complete(acme(), tried) is tried
    no_email = Contact(first_name="Jane", last_name="Doe", data={"apollo_id": "p1", "apollo_has_email": False})
    assert finder.complete(acme(), no_email) is no_email
    off = ApolloFinder({"reveal_emails": False}, ctx)
    assert off.complete(acme(), Contact(first_name="A", last_name="B")).email == ""
    assert ctx.http.calls == []


def test_apollo_complete_limit_per_company(make_ctx):
    ctx = make_ctx(env={"APOLLO_API_KEY": "k"})
    ctx.http.add("POST", APOLLO_MATCH, json={"person": None})
    finder = ApolloFinder({"complete_limit": 2}, ctx)
    for i in range(4):
        finder.complete(acme(), Contact(first_name=f"P{i}", last_name="X"))
    assert len(ctx.http.calls_to("/people/match")) == 2
    finder.complete(acme(name="Globex", domain="globex.com"), Contact(first_name="G", last_name="X"))
    assert len(ctx.http.calls_to("/people/match")) == 3  # budget is per company
    none = ApolloFinder({"complete_limit": 0}, ctx)
    none.complete(acme(), Contact(first_name="Z", last_name="X"))
    assert len(ctx.http.calls_to("/people/match")) == 3
    with pytest.raises(ValueError, match="complete_limit"):
        ApolloFinder({"complete_limit": "x"}, ctx).complete(acme(), Contact(first_name="Z", last_name="X"))


def test_apollo_complete_not_found_and_errors(make_ctx):
    ctx = make_ctx(env={"APOLLO_API_KEY": "k"})
    ctx.http.add("POST", APOLLO_MATCH, status=404, json={"error": "no match"}, times=1)
    ctx.http.add("POST", APOLLO_MATCH, status=429, json={"error": "rate limited"}, times=1)
    finder = ApolloFinder({}, ctx)
    c = Contact(first_name="Jane", last_name="Doe")
    assert finder.complete(acme(), c).email == "" and c.data["apollo_revealed"] is True
    with pytest.raises(HttpError):
        finder.complete(acme(), Contact(first_name="Bob", last_name="Stone"))


def test_apollo_complete_ignores_placeholder_email(make_ctx):
    ctx = make_ctx(env={"APOLLO_API_KEY": "k"})
    ctx.http.add("POST", APOLLO_MATCH, json=apollo_match_payload("p1", "Jane", "Doe", "CFO",
                                                                 "email_not_unlocked@domain.com"))
    c = ApolloFinder({}, ctx).complete(acme(), Contact(first_name="Jane", last_name="Doe"))
    assert c.email == "" and c.email_status == EmailStatus.UNKNOWN


def test_apollo_bad_config_values(make_ctx):
    ctx = make_ctx(env={"APOLLO_API_KEY": "k"})
    with pytest.raises(ValueError, match="per_page"):
        ApolloFinder({"per_page": "many"}, ctx).find(acme())
    with pytest.raises(ValueError, match="filters"):
        ApolloFinder({"filters": ["x"]}, ctx).find(acme())


def test_apollo_complete_forbidden_blocks_match_but_not_search(make_ctx):
    # a 401/402/403 from people/match inside complete() must not take the whole finder down:
    # it blocks people/match (like a failed reveal) and returns the contact unchanged
    ctx = make_ctx(env={"APOLLO_API_KEY": "k"}, buyers=BUYERS)
    ctx.http.add("POST", APOLLO_MATCH, status=402, json={"error": "insufficient credits"})
    ctx.http.add("POST", APOLLO_SEARCH, json=apollo_search_payload(
        apollo_person("p2", "Bo", "Li", "CFO", email="bo@acme.com", email_status="verified")))
    finder = ApolloFinder({}, ctx)
    jane = Contact(first_name="Jane", last_name="Doe", title="CFO")
    assert finder.complete(acme(), jane) is jane and jane.email == ""
    assert finder.complete(acme(), Contact(first_name="Bob", last_name="Stone")).email == ""
    assert len(ctx.http.calls_to("/people/match")) == 1  # blocked after the refusal
    (bo,) = finder.find(acme(name="Beta", domain="beta.com"))  # the search still runs
    assert bo.email == "bo@acme.com" and len(ctx.http.calls_to("/mixed_people/")) == 1


def test_apollo_reveal_non_json_response_keeps_people(make_ctx):
    ctx = make_ctx(env={"APOLLO_API_KEY": "k"}, buyers=BUYERS)
    ctx.http.add("POST", APOLLO_SEARCH, json=apollo_search_payload(
        {"id": "p1", "first_name": "Jane", "last_name_obfuscated": "Do***e", "title": "CFO", "has_email": True},
        apollo_person("p2", "Bob", "Li", "CFO", email="bob@acme.com", email_status="verified")))
    ctx.http.add("POST", APOLLO_MATCH, text="<html>upstream error</html>")
    found = ApolloFinder({}, ctx).find(acme())
    assert [c.first_name for c in found] == ["Jane", "Bob"]
    assert found[1].email == "bob@acme.com" and found[1].email_status == EmailStatus.VALID
    with pytest.raises(ValueError, match="not JSON"):  # complete(): a clear error, nothing lost
        ApolloFinder({}, ctx).complete(acme(), Contact(first_name="Jane", last_name="Doe"))


def test_apollo_find_reveals_count_toward_the_company_budget(make_ctx):
    ctx = make_ctx(env={"APOLLO_API_KEY": "k"}, buyers={"titles": ["CFO"]})
    ctx.http.add("POST", APOLLO_SEARCH, json=apollo_search_payload(*[
        {"id": f"p{i}", "first_name": f"N{i}", "last_name_obfuscated": "X***y", "title": "CFO", "has_email": True}
        for i in range(6)]))
    ctx.http.add("POST", APOLLO_MATCH, fn=lambda call: {"person": {"id": call["json"].get("id"), "email": None}})
    finder = ApolloFinder({"reveal_limit": 2}, ctx)
    company = acme()
    found = finder.find(company)
    assert len(ctx.http.calls_to("/people/match")) == 2
    for contact in found:  # what the waterfall does next for the email-less targets
        finder.complete(company, contact)
    # one people/match budget per company: max(reveal_limit, complete_limit) = 3, not 2 + 3
    assert len(ctx.http.calls_to("/people/match")) == 3
    finder.complete(acme(name="Globex", domain="globex.com"), Contact(first_name="G", last_name="X"))
    assert len(ctx.http.calls_to("/people/match")) == 4  # other companies keep their own budget


# === Hunter =====================================================================================

def hunter_email(value, first, last, position, status="valid", confidence=94, **extra):
    e = {"value": value, "type": "personal", "confidence": confidence,
         "sources": [{"domain": "acme.com", "uri": "https://acme.com/team", "extracted_on": "2026-01-01",
                      "last_seen_on": "2026-09-01", "still_on_page": True}],
         "first_name": first, "last_name": last, "position": position, "position_raw": position,
         "seniority": "executive", "department": "finance",
         "linkedin": f"https://www.linkedin.com/in/{first.lower()}{last.lower()}", "twitter": None,
         "phone_number": None, "verification": {"date": "2026-09-01", "status": status}}
    e.update(extra)
    return e


def hunter_domain_payload(*emails, pattern="{first}.{last}", accept_all=False, domain="acme.com"):
    return {"data": {"domain": domain, "disposable": False, "webmail": False, "accept_all": accept_all,
                     "pattern": pattern, "organization": "Acme", "description": None, "industry": None,
                     "twitter": None, "facebook": None, "linkedin": None, "country": "US", "state": "TX",
                     "city": "Austin", "emails": list(emails), "linked_domains": []},
            "meta": {"results": len(emails), "limit": 10, "offset": 0,
                     "params": {"domain": domain, "company": None, "type": "personal", "seniority": None,
                                "department": None}}}


def test_hunter_domain_search_request_shape(make_ctx):
    ctx = make_ctx(env={"HUNTER_API_KEY": "h-key"}, buyers=BUYERS)
    ctx.http.add("GET", HUNTER_DOMAIN, json=hunter_domain_payload())
    HunterFinder({"seniority": ["executive", "senior", "boss"]}, ctx).find(acme())
    call = ctx.http.calls[0]
    assert call["method"] == "GET" and call["url"] == HUNTER_DOMAIN
    # "executive" joins buyers.departments: the CFO buyer title is an executive one
    assert call["params"] == {"domain": "acme.com", "limit": 10, "type": "personal",
                              "seniority": "executive,senior", "department": "finance,hr,executive",
                              "api_key": "h-key"}


def test_hunter_search_by_company_name_and_options(make_ctx):
    ctx = make_ctx(env={"HUNTER_API_KEY": "h-key"}, buyers=BUYERS)
    ctx.http.add("GET", HUNTER_DOMAIN, json=hunter_domain_payload(domain="acme-corp.com"))
    company = Company(name="Acme Corp")
    HunterFinder({"limit": 250, "email_type": "all", "department": "Engineering, sales",
                  "params": {"required_field": "full_name"}}, ctx).find(company)
    params = ctx.http.calls[0]["params"]
    assert params["company"] == "Acme Corp" and "domain" not in params
    assert params["limit"] == 100 and "type" not in params
    assert params["department"] == "it,sales" and params["required_field"] == "full_name"
    assert company.data["email_domain"] == "acme-corp.com"  # hint for pattern / email-finder
    assert company.domain == ""


def test_hunter_no_departments_when_disabled(make_ctx):
    ctx = make_ctx(env={"HUNTER_API_KEY": "h-key"}, buyers=BUYERS)
    ctx.http.add("GET", HUNTER_DOMAIN, json=hunter_domain_payload())
    HunterFinder({"use_buyer_departments": False}, ctx).find(acme())
    assert "department" not in ctx.http.calls[0]["params"]


def test_hunter_buyer_departments_keep_executive_buyers(make_ctx):
    # saas-funding style buyers: founders / CEOs sit in Hunter's "executive" department, which a
    # department=marketing,sales filter would exclude
    buyers = {"titles": ["Founder", "Co-founder", "CEO", "Head of Growth", "VP Marketing"],
              "departments": ["marketing", "sales"]}
    ctx = make_ctx(env={"HUNTER_API_KEY": "h-key"}, buyers=buyers)
    assert HunterFinder({}, ctx).search_params(acme())["department"] == "marketing,sales,executive"
    ctx = make_ctx(env={"HUNTER_API_KEY": "h-key"}, buyers=dict(buyers, titles=["Head of Growth", "VP Sales"]))
    assert HunterFinder({}, ctx).search_params(acme())["department"] == "marketing,sales"
    # an explicit department config is used as given
    assert HunterFinder({"department": "sales"}, ctx).search_params(acme())["department"] == "sales"
    assert executive_departments(["Managing Director"]) == ["executive", "management"]
    assert executive_departments(["Vice President of Sales", "Head of Sales"]) == []
    assert executive_departments(["Practice Owner"]) == ["executive"]


def test_hunter_skips_company_without_domain_or_name(make_ctx):
    ctx = make_ctx(env={"HUNTER_API_KEY": "h-key"})
    assert HunterFinder({}, ctx).find(Company(name="")) == []
    assert ctx.http.calls == []


def test_hunter_maps_emails_and_hints(make_ctx):
    ctx = make_ctx(env={"HUNTER_API_KEY": "h-key"})
    ctx.http.add("GET", HUNTER_DOMAIN, json=hunter_domain_payload(
        hunter_email("Jane.Doe@acme.com", "Jane", "Doe", "Chief Financial Officer", "valid", 97,
                     phone_number="+1 512 555 0100"),
        hunter_email("bob.stone@acme.com", "Bob", "Stone", "Controller", "accept_all", 80, linkedin="bobstone"),
        hunter_email("carl@acme.com", "Carl", "Ctrl", "Accountant", "invalid", 40),
        hunter_email("dee@acme.com", "Dee", "Dee", "Analyst", None, None, verification=None, linkedin=None),
        hunter_email("not an email", "", "", "Unknown"),
        "junk",
        pattern="{f}{last}"))
    company = acme()
    found = HunterFinder({}, ctx).find(company)
    assert [c.full_name for c in found] == ["Jane Doe", "Bob Stone", "Carl Ctrl", "Dee Dee"]
    jane, bob, carl, dee = found
    assert all(c.source == "hunter" for c in found)
    assert jane.email == "jane.doe@acme.com" and jane.email_status == EmailStatus.VALID
    assert jane.confidence == pytest.approx(0.97) and jane.title == "Chief Financial Officer"
    assert jane.phone == "+1 512 555 0100" and jane.seniority == "executive" and jane.department == "finance"
    assert jane.linkedin_url == "https://www.linkedin.com/in/janedoe"
    assert jane.data["hunter_verification"] == "valid" and jane.data["hunter_type"] == "personal"
    assert bob.email_status == EmailStatus.RISKY and bob.linkedin_url == "https://www.linkedin.com/in/bobstone"
    assert carl.email_status == EmailStatus.INVALID
    assert dee.email_status == EmailStatus.UNKNOWN and dee.confidence is None and dee.linkedin_url == ""
    assert company.data == {"email_pattern": "{f}{last}", "email_accept_all": False}


def test_hunter_accept_all_domain_makes_unverified_risky(make_ctx):
    ctx = make_ctx(env={"HUNTER_API_KEY": "h-key"})
    ctx.http.add("GET", HUNTER_DOMAIN, json=hunter_domain_payload(
        hunter_email("jane@acme.com", "Jane", "Doe", "CFO", None), accept_all=True, pattern=None))
    company = acme()
    (jane,) = HunterFinder({}, ctx).find(company)
    assert jane.email_status == EmailStatus.RISKY
    assert company.data == {"email_accept_all": True}  # no pattern -> none saved


def test_hunter_find_errors(make_ctx):
    ctx = make_ctx(env={"HUNTER_API_KEY": "h-key"})
    ctx.http.add("GET", HUNTER_DOMAIN, json={"errors": [{"id": "x"}]}, times=1)
    ctx.http.add("GET", HUNTER_DOMAIN, status=401, json={"errors": [{"id": "authentication_failed", "code": 401,
                                                                     "details": "No user found for the API key"}]})
    finder = HunterFinder({}, ctx)
    with pytest.raises(ValueError, match="unexpected domain-search response"):
        finder.find(acme())
    with pytest.raises(HttpError) as ei:
        finder.find(acme())
    assert ei.value.status == 401


def test_hunter_network_errors_do_not_leak_api_key(make_ctx):
    def network_error(call):
        query = "&".join(f"{k}={v}" for k, v in call["params"].items())
        raise HttpError(0, call["url"], f"ConnectionError: Max retries exceeded with url: /v2/x?{query}")

    ctx = make_ctx(env={"HUNTER_API_KEY": "SECRET-KEY-123"})
    ctx.http.add("GET", "https://api.hunter.io/", fn=network_error)
    finder = HunterFinder({}, ctx)
    for call in (lambda: finder.find(acme()), lambda: finder.complete(acme(), Contact(first_name="J", last_name="D"))):
        with pytest.raises(HttpError) as ei:
            call()
        assert ei.value.status == 0 and "SECRET-KEY-123" not in str(ei.value)
    assert all(c["params"]["api_key"] == "SECRET-KEY-123" for c in ctx.http.calls)


def test_hunter_empty_emails(make_ctx):
    ctx = make_ctx(env={"HUNTER_API_KEY": "h-key"})
    ctx.http.add("GET", HUNTER_DOMAIN, json={"data": {"domain": "acme.com", "emails": None}, "meta": {}})
    assert HunterFinder({}, ctx).find(acme()) == []


def test_hunter_missing_credentials_and_dry_run(make_ctx):
    ctx = make_ctx(env={})
    finder = HunterFinder({}, ctx)
    with pytest.raises(MissingCredentialError, match="HUNTER_API_KEY"):
        finder.find(acme())
    with pytest.raises(MissingCredentialError):
        finder.complete(acme(), Contact(first_name="Jane", last_name="Doe"))
    dry = make_ctx(env={"HUNTER_API_KEY": "k"}, dry_run=True)
    finder = HunterFinder({}, dry)
    assert finder.find(acme()) == []
    assert finder.complete(acme(), Contact(first_name="Jane", last_name="Doe")).email == ""
    assert ctx.http.calls == [] and dry.http.calls == []


def test_hunter_bad_config(make_ctx):
    ctx = make_ctx(env={"HUNTER_API_KEY": "k"})
    with pytest.raises(ValueError, match="limit"):
        HunterFinder({"limit": "lots"}, ctx).find(acme())
    with pytest.raises(ValueError, match="params"):
        HunterFinder({"params": "x=1"}, ctx).find(acme())


def hunter_finder_payload(email="jane.doe@acme.com", score=92, status="valid"):
    return {"data": {"first_name": "Jane", "last_name": "Doe", "email": email, "score": score,
                     "domain": "acme.com", "accept_all": False, "position": "CFO", "twitter": None,
                     "linkedin_url": "https://www.linkedin.com/in/janedoe", "phone_number": None,
                     "company": "Acme", "sources": [],
                     "verification": {"date": "2026-09-01", "status": status}},
            "meta": {"params": {"first_name": "Jane", "last_name": "Doe", "full_name": None,
                                "domain": "acme.com", "company": None}}}


def test_hunter_complete(make_ctx):
    ctx = make_ctx(env={"HUNTER_API_KEY": "h-key"})
    ctx.http.add("GET", HUNTER_FINDER, json=hunter_finder_payload())
    c = Contact(first_name="Jane", last_name="Doe", source="apollo")
    out = HunterFinder({}, ctx).complete(acme(), c)
    assert out is c
    assert ctx.http.calls[0]["params"] == {"domain": "acme.com", "first_name": "Jane", "last_name": "Doe",
                                           "api_key": "h-key"}
    assert c.email == "jane.doe@acme.com" and c.email_status == EmailStatus.VALID
    assert c.confidence == pytest.approx(0.92) and c.title == "CFO" and c.source == "apollo"
    assert c.linkedin_url == "https://www.linkedin.com/in/janedoe" and c.data["email_source"] == "hunter"


@pytest.mark.parametrize("status,expected", [("accept_all", EmailStatus.RISKY), ("invalid", EmailStatus.INVALID),
                                             ("unknown", EmailStatus.UNKNOWN), (None, EmailStatus.UNKNOWN)])
def test_hunter_complete_status_mapping(make_ctx, status, expected):
    ctx = make_ctx(env={"HUNTER_API_KEY": "h-key"})
    ctx.http.add("GET", HUNTER_FINDER, json=hunter_finder_payload(status=status))
    c = HunterFinder({}, ctx).complete(acme(), Contact(first_name="Jane", last_name="Doe"))
    assert c.email_status == expected


def test_hunter_complete_uses_company_name_or_domain_hint(make_ctx):
    ctx = make_ctx(env={"HUNTER_API_KEY": "h-key"})
    ctx.http.add("GET", HUNTER_FINDER, json=hunter_finder_payload())
    finder = HunterFinder({}, ctx)
    finder.complete(Company(name="Acme Corp"), Contact(first_name="Jane", last_name="Doe"))
    assert ctx.http.calls[-1]["params"]["company"] == "Acme Corp"
    finder.complete(Company(name="Acme Corp", data={"email_domain": "acme-corp.com"}),
                    Contact(first_name="Jane", last_name="Doe"))
    assert ctx.http.calls[-1]["params"]["domain"] == "acme-corp.com"


def test_hunter_complete_skips_without_names(make_ctx):
    ctx = make_ctx(env={"HUNTER_API_KEY": "h-key"})
    finder = HunterFinder({}, ctx)
    for c in (Contact(first_name="Jane"), Contact(first_name="Priya", last_name="Pa***l"),
              Contact(first_name="Jane", last_name="Doe", email="jane@acme.com")):
        assert finder.complete(acme(), c) is c
    assert finder.complete(Company(name=""), Contact(first_name="Jane", last_name="Doe")).email == ""
    assert ctx.http.calls == []


@pytest.mark.parametrize("http_status", [400, 404, 422, 451])
def test_hunter_complete_soft_errors_leave_contact(make_ctx, http_status):
    ctx = make_ctx(env={"HUNTER_API_KEY": "h-key"})
    ctx.http.add("GET", HUNTER_FINDER, status=http_status, json={"errors": [{"code": http_status}]})
    c = Contact(first_name="Jane", last_name="Doe")
    assert HunterFinder({}, ctx).complete(acme(), c) is c and c.email == ""


@pytest.mark.parametrize("http_status", [401, 403, 429, 500])
def test_hunter_complete_hard_errors_raise(make_ctx, http_status):
    ctx = make_ctx(env={"HUNTER_API_KEY": "h-key"})
    ctx.http.add("GET", HUNTER_FINDER, status=http_status, json={"errors": [{"code": http_status}]})
    with pytest.raises(HttpError):
        HunterFinder({}, ctx).complete(acme(), Contact(first_name="Jane", last_name="Doe"))


@pytest.mark.parametrize("payload", [{"data": {"email": None, "score": None}}, {"data": None}, {"data": {"email": "x"}}])
def test_hunter_complete_no_result(make_ctx, payload):
    ctx = make_ctx(env={"HUNTER_API_KEY": "h-key"})
    ctx.http.add("GET", HUNTER_FINDER, json=payload)
    c = HunterFinder({}, ctx).complete(acme(), Contact(first_name="Jane", last_name="Doe"))
    assert c.email == "" and c.email_status == EmailStatus.UNKNOWN


def test_hunter_helpers():
    assert hunter_departments("Human Resources") == ["hr"]
    assert hunter_departments("Business Development") == ["sales"]
    assert hunter_departments("People Operations") == ["hr"]
    assert hunter_departments("Sales & Marketing") == ["sales", "marketing"]
    assert hunter_departments("C-Suite") == ["executive"]
    assert hunter_departments("it") == ["it"]
    assert hunter_departments("Underwater basket weaving") == []
    assert hunter_departments("") == []
    assert verification_status("valid") == EmailStatus.VALID
    assert verification_status("accept_all") == EmailStatus.RISKY
    assert verification_status(None, accept_all=True) == EmailStatus.RISKY
    assert verification_status("invalid", accept_all=True) == EmailStatus.INVALID
    assert linkedin_url("janedoe") == "https://www.linkedin.com/in/janedoe"
    assert linkedin_url("linkedin.com/in/jd") == "https://linkedin.com/in/jd"
    assert linkedin_url("https://www.linkedin.com/in/jd") == "https://www.linkedin.com/in/jd"
    assert linkedin_url(None) == ""


# === Pattern ====================================================================================

def test_pattern_default_order(make_ctx):
    finder = PatternFinder({}, make_ctx())
    assert finder.offline is True and finder.find(acme()) == []
    c = finder.complete(acme(), Contact(first_name="Jane", last_name="Doe"))
    assert c.email == ""
    assert c.email_candidates == ["jane.doe@acme.com", "jane@acme.com", "jdoe@acme.com", "janedoe@acme.com",
                                  "j.doe@acme.com", "jane_doe@acme.com", "janed@acme.com", "doe@acme.com"]
    assert c.data["email_candidates_source"] == "pattern"


def test_pattern_company_pattern_first(make_ctx):
    finder = PatternFinder({}, make_ctx())
    company = acme(data={"email_pattern": "{f}{last}"})
    c = finder.complete(company, Contact(first_name="Jane", last_name="Doe"))
    assert c.email_candidates[0] == "jdoe@acme.com"
    assert c.email_candidates.count("jdoe@acme.com") == 1 and len(c.email_candidates) == 8
    off = PatternFinder({"use_company_pattern": False}, make_ctx())
    assert off.candidates(company, Contact(first_name="Jane", last_name="Doe"))[0] == "jane.doe@acme.com"


def test_pattern_hunter_style_uncommon_company_pattern(make_ctx):
    finder = PatternFinder({}, make_ctx())
    c = finder.complete(acme(data={"email_pattern": "{last}.{first}"}), Contact(first_name="Jane", last_name="Doe"))
    assert c.email_candidates[0] == "doe.jane@acme.com" and len(c.email_candidates) == 9


def test_pattern_accents_hyphens_and_titles(make_ctx):
    finder = PatternFinder({}, make_ctx())
    c = finder.complete(acme(), Contact(first_name="José", last_name="Smith-Jones"))
    assert c.email_candidates[:3] == ["jose.smithjones@acme.com", "jose@acme.com", "jsmithjones@acme.com"]
    c = finder.complete(acme(), Contact(first_name="Jürgen", last_name="Groß"))
    assert c.email_candidates[0] == "jurgen.gross@acme.com"
    c = finder.complete(acme(), Contact(full_name="Dr. Anne-Marie van der Berg, PhD"))
    assert c.email_candidates[0] == "annemarie.vanderberg@acme.com"
    c = finder.complete(acme(), Contact(first_name="Seán", last_name="O'Brien (He/Him)"))
    assert c.email_candidates[0] == "sean.obrien@acme.com"


def test_pattern_only_first_name(make_ctx):
    finder = PatternFinder({}, make_ctx())
    c = finder.complete(acme(data={"email_pattern": "{first}.{last}"}), Contact(first_name="Jane"))
    assert c.email_candidates == ["jane@acme.com"]
    obfuscated = finder.complete(acme(), Contact(first_name="Priya", last_name="Pa***l"))
    assert obfuscated.email_candidates == ["priya@acme.com"]


def test_pattern_no_candidates(make_ctx):
    finder = PatternFinder({}, make_ctx())
    has_email = Contact(first_name="Jane", last_name="Doe", email="jane@acme.com")
    assert finder.complete(acme(), has_email).email_candidates == []
    assert finder.complete(Company(name="No Domain"), Contact(first_name="Jane", last_name="Doe")).email_candidates == []
    assert finder.complete(acme(), Contact(last_name="Doe")).email_candidates == []
    assert finder.complete(acme(), Contact(first_name="李", last_name="王")).email_candidates == []


def test_pattern_uses_email_domain_hint(make_ctx):
    finder = PatternFinder({}, make_ctx())
    c = finder.complete(Company(name="Acme", data={"email_domain": "acme-corp.com"}),
                        Contact(first_name="Jane", last_name="Doe"))
    assert c.email_candidates[0] == "jane.doe@acme-corp.com"


def test_pattern_keeps_existing_candidates_after_known_pattern(make_ctx):
    finder = PatternFinder({}, make_ctx())
    c = Contact(first_name="Jane", last_name="Doe", email_candidates=["jd@acme.com", "jane.doe@acme.com"])
    finder.complete(acme(data={"email_pattern": "{f}{last}"}), c)
    assert c.email_candidates[:3] == ["jdoe@acme.com", "jd@acme.com", "jane.doe@acme.com"]
    assert len(c.email_candidates) == len(set(c.email_candidates))


def test_pattern_custom_patterns_and_cap(make_ctx):
    finder = PatternFinder({"patterns": ["lastf", "{first}-{last}", "bogus", "{nope}"], "max_candidates": 2},
                           make_ctx())
    c = finder.complete(acme(), Contact(first_name="Jane", last_name="Doe"))
    assert c.email_candidates == ["doej@acme.com", "jane-doe@acme.com"]
    single = PatternFinder({"patterns": "first.last"}, make_ctx())
    assert single.candidates(acme(), Contact(first_name="Jane", last_name="Doe")) == ["jane.doe@acme.com"]


def test_pattern_bad_max_candidates(make_ctx):
    with pytest.raises(ValueError, match="max_candidates"):
        PatternFinder({"max_candidates": "few"}, make_ctx()).candidates(acme(), Contact(first_name="J", last_name="D"))


def test_pattern_whole_name_in_first_name_field(make_ctx):
    finder = PatternFinder({}, make_ctx())
    c = finder.complete(acme(), Contact(first_name="Jane Doe"))
    assert c.email_candidates[0] == "jane.doe@acme.com"


@pytest.mark.parametrize("contact", [
    Contact(full_name="Prof. Dr. Hans Meier"), Contact(first_name="Prof.", last_name="Dr. Hans Meier"),
    Contact(full_name="Dr. Dr. Hans Meier"), Contact(full_name="Mr. Dr. Hans Meier"),
    Contact(first_name="Prof. Dr.", last_name="Hans Meier"), Contact(full_name="Meier, Hans"),
    Contact(full_name="Meier, Prof. Dr. Hans"),
])
def test_pattern_stacked_honorifics_and_last_first(make_ctx, contact):
    finder = PatternFinder({}, make_ctx())
    candidates = finder.candidates(Company(name="Klinik", domain="klinik.de"), contact)
    assert candidates[:3] == ["hans.meier@klinik.de", "hans@klinik.de", "hmeier@klinik.de"]


def test_pattern_honorifics_only_leave_no_first_name():
    assert contact_name_parts(Contact(full_name="Prof. Dr. Meier")) == ("", "meier")
    assert contact_name_parts(Contact(first_name="Prof", last_name="Dr Doe")) == ("", "doe")


@pytest.mark.parametrize("raw,expected", [
    ("Dr. Jane Doe", ("Jane", "Doe")), ("Ms Jane Doe", ("Jane", "Doe")), ("Doe, Jane", ("Jane", "Doe")),
    ("Prof. Dr. Hans Meier", ("Hans", "Meier")), ("Jane Doe, PhD", ("Jane", "Doe")),
    ("Doe, Jane, CPA", ("Jane", "Doe")), ("van der Berg, Anna", ("Anna", "van der Berg")),
    ("Anne-Marie van der Berg", ("Anne-Marie", "van der Berg")), ("Jane Doe, CFO", ("Jane", "Doe")),
    ("John Smith Jr.", ("John", "Smith")), ("Jane Doe (She/Her)", ("Jane", "Doe")),
    ("Dr Doe", ("", "Doe")), ("Jane", ("Jane", "")), ("Dr.", ("", "")), ("", ("", "")), (None, ("", "")),
])
def test_split_full_name(raw, expected):
    assert split_full_name(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("{first}.{last}", "{first}.{last}"), ("{F}{Last}", "{f}{last}"), ("first.last", "{first}.{last}"),
    ("flast", "{f}{last}"), ("firstl", "{first}{l}"), ("first_last", "{first}_{last}"),
    ("firstname.lastname", "{first}.{last}"), ("{first_initial}{last_name}", "{f}{last}"),
    ("{first}.{last}@acme.com", "{first}.{last}"), ("{first} {last}", None), ("{x}", None), ("abc", None),
    ("", None), (None, None), (123, None),
])
def test_normalize_pattern(raw, expected):
    assert normalize_pattern(raw) == expected


def test_render_and_name_helpers():
    assert render_pattern("{f}{last}", "jane", "doe") == "jdoe"
    assert render_pattern("{first}.{last}", "jane", "") is None
    assert render_pattern("{first}", "jane", "") == "jane"
    assert name_token("Smith-Jones") == "smithjones"
    assert name_token("Doe, CPA") == "doe"
    assert name_token("Doe Jr") == "doe"
    assert name_token("Dr. Jane", is_first=True) == "jane"
    assert contact_name_parts(Contact(first_name="Dr.", last_name="Jane Doe")) == ("jane", "doe")
    assert contact_name_parts(Contact(first_name="Dr. Jane")) == ("jane", "")
    assert contact_name_parts(Contact(first_name="Dr Jane Doe")) == ("jane", "doe")
    assert contact_name_parts(Contact(first_name="Dr.", last_name="Doe")) == ("", "doe")
    assert contact_name_parts(Contact(last_name="Doe")) == ("", "doe")
    assert contact_name_parts(Contact(full_name="Jane Doe")) == ("jane", "doe")
    assert contact_name_parts(Contact(first_name="Jane", full_name="Jane van Dyke")) == ("jane", "vandyke")


# === CSV ========================================================================================

CSV_TEXT = """Company Name,Website,First Name,Last Name,Job Title,Email,Email Status,LinkedIn URL,Phone,Notes
Acme Inc,https://www.acme.com,Jane,Doe,CFO,Jane.Doe@acme.com,Verified,https://linkedin.com/in/janedoe,+1 555 0100,met at expo
Acme Inc,acme.com,Bob,Stone,Controller,bob@acme.com,catch-all,,,
"Acme, Inc.",,Carl,Nodomain,Accountant,,,,,
Acme Inc,acme.io,Other,Acme,CEO,other@acme.io,valid,,,
Globex,,Gina,Glo,VP Finance,gina@globex.com,bounced,,,
,,Solo,Person,Owner,solo@solo.dev,,,,
Acme Inc,acme.com,,,,,,,,
Beta LLC,beta.com,Bad,Email,CFO,not-an-email,valid,,,
"""


def write(tmp_path: Path, text: str, name: str = "contacts.csv", encoding: str = "utf-8") -> Path:
    p = tmp_path / name
    p.write_bytes(text.encode(encoding))
    return p


def test_csv_find_by_domain(make_ctx, tmp_path):
    path = write(tmp_path, CSV_TEXT)
    finder = CsvFinder({"path": str(path)}, make_ctx())
    assert finder.offline is True
    found = finder.find(acme())
    # acme.com rows + the same-name row without a domain; acme.io is another company
    assert [c.full_name for c in found] == ["Jane Doe", "Bob Stone", "Carl Nodomain"]
    jane, bob, carl = found
    assert all(c.source == "csv" for c in found)
    assert jane.email == "jane.doe@acme.com" and jane.email_status == EmailStatus.VALID
    assert jane.title == "CFO" and jane.linkedin_url == "https://linkedin.com/in/janedoe"
    assert jane.phone == "+1 555 0100" and jane.data["csv"] == {"notes": "met at expo"}
    assert bob.email_status == EmailStatus.RISKY
    assert carl.email == "" and carl.email_status == EmailStatus.UNKNOWN


def test_csv_find_by_name_when_company_has_no_domain(make_ctx, tmp_path):
    finder = CsvFinder({"path": str(write(tmp_path, CSV_TEXT))}, make_ctx())
    found = finder.find(Company(name="ACME Inc."))
    assert {c.full_name for c in found} == {"Jane Doe", "Bob Stone", "Carl Nodomain", "Other Acme"}
    gina = finder.find(Company(name="Globex"))
    assert len(gina) == 1 and gina[0].email_status == EmailStatus.INVALID


def test_csv_email_domain_fallback_and_bad_email(make_ctx, tmp_path):
    finder = CsvFinder({"path": str(write(tmp_path, CSV_TEXT))}, make_ctx())
    assert [c.full_name for c in finder.find(Company(name="Globex Corp", domain="globex.com"))] == ["Gina Glo"]
    assert [c.full_name for c in finder.find(Company(name="Solo", domain="solo.dev"))] == ["Solo Person"]
    (bad,) = finder.find(Company(name="Beta", domain="beta.com"))
    assert bad.email == "" and bad.email_status == EmailStatus.UNKNOWN
    assert finder.find(Company(name="Nobody", domain="nobody.com")) == []


def test_csv_loads_once_and_returns_fresh_copies(make_ctx, tmp_path):
    path = write(tmp_path, CSV_TEXT)
    finder = CsvFinder({"path": str(path)}, make_ctx())
    first = finder.find(acme())
    first[0].email = "changed@acme.com"
    path.unlink()  # a second call must not re-read the file
    again = finder.find(acme())
    assert again[0].email == "jane.doe@acme.com" and again[0] is not first[0]


def test_csv_semicolon_mapping_limit_and_relative_path(make_ctx, tmp_path, monkeypatch):
    text = ("Org;Domain;Person;Role;Mail;Stat\n"
            "Acme;acme.com;Jane Doe;CFO;jane@acme.com;ok\n"
            "Acme;acme.com;John Roe;CTO;john@acme.com;unknown\n")
    write(tmp_path, text, "people.csv")
    monkeypatch.chdir(tmp_path)
    finder = CsvFinder({"path": "people.csv", "limit": 1,
                        "mapping": {"company": "Org", "full_name": "Person", "title": "Role", "email": "Mail",
                                    "email_status": ["Missing", "Stat"]}}, make_ctx())
    (jane,) = finder.find(acme())
    assert jane.first_name == "Jane" and jane.last_name == "Doe" and jane.title == "CFO"
    assert jane.email_status == EmailStatus.VALID


def test_csv_mapping_can_disable_a_column(make_ctx, tmp_path):
    finder = CsvFinder({"path": str(write(tmp_path, CSV_TEXT)), "mapping": {"email_status": None}}, make_ctx())
    assert finder.find(acme())[0].email_status == EmailStatus.UNKNOWN


def test_csv_cp1252_fallback_and_tab_delimiter(make_ctx, tmp_path):
    text = "company\tdomain\tfirst name\tlast name\temail\nAcmé\tacme.com\tJosé\tNuñez\tjose@acme.com\n"
    path = write(tmp_path, text, encoding="cp1252")
    (jose,) = CsvFinder({"path": str(path), "delimiter": "tab"}, make_ctx()).find(acme())
    assert jose.first_name == "José" and jose.last_name == "Nuñez"


def test_csv_errors(make_ctx, tmp_path):
    ctx = make_ctx()
    with pytest.raises(ValueError, match="'path' is required"):
        CsvFinder({}, ctx).find(acme())
    with pytest.raises(FileNotFoundError, match="not found"):
        CsvFinder({"path": str(tmp_path / "missing.csv")}, ctx).find(acme())
    no_cols = write(tmp_path, "foo,bar\n1,2\n", "bad.csv")
    with pytest.raises(ValueError, match="needs a company, domain, website or email column"):
        CsvFinder({"path": str(no_cols)}, ctx).find(acme())
    with pytest.raises(ValueError, match="unknown mapping field"):
        CsvFinder({"path": str(write(tmp_path, CSV_TEXT)), "mapping": {"nickname": "x"}}, ctx).find(acme())
    with pytest.raises(ValueError, match="delimiter"):
        CsvFinder({"path": str(write(tmp_path, CSV_TEXT)), "delimiter": "::"}, ctx).find(acme())


def test_csv_ignores_linkedin_url_as_company_website(make_ctx, tmp_path):
    text = "company,website,full name,title\nAcme,https://www.linkedin.com/company/acme,Jane Doe,CFO\n"
    finder = CsvFinder({"path": str(write(tmp_path, text))}, make_ctx())
    assert finder.find(Company(name="LinkedIn", domain="linkedin.com")) == []
    assert [c.full_name for c in finder.find(acme())] == ["Jane Doe"]  # matched by name (no own domain)


@pytest.mark.parametrize("raw,expected", [
    ("Verified", EmailStatus.VALID), ("deliverable", EmailStatus.VALID), ("Catch-All", EmailStatus.RISKY),
    ("accept_all", EmailStatus.RISKY), ("Bounced", EmailStatus.INVALID), ("undeliverable", EmailStatus.INVALID),
    ("", EmailStatus.UNKNOWN), ("maybe", EmailStatus.UNKNOWN),
    # "never mail" verdicts of list cleaners (ZeroBounce / NeverBounce / MillionVerifier exports)
    ("spamtrap", EmailStatus.INVALID), ("Spam Trap", EmailStatus.INVALID), ("do_not_mail", EmailStatus.INVALID),
    ("abuse", EmailStatus.INVALID), ("disposable", EmailStatus.INVALID), ("hard_bounce", EmailStatus.INVALID),
    ("complainer", EmailStatus.INVALID), ("toxic", EmailStatus.INVALID), ("Unsubscribed", EmailStatus.INVALID),
    ("Invalid - mailbox not found", EmailStatus.INVALID), ("not valid", EmailStatus.INVALID),
    ("Not verified", EmailStatus.UNKNOWN), ("unverified", EmailStatus.UNKNOWN), ("unknown", EmailStatus.UNKNOWN),
])
def test_csv_status_values(raw, expected):
    assert map_status(raw) == expected


def test_csv_spamtrap_row_is_invalid_and_keeps_raw_status(make_ctx, tmp_path):
    text = ("first name,last name,email,company,website,email status\n"
            "Jane,Doe,jane@acme.com,Acme,acme.com,spamtrap\n"
            "Bob,Stone,bob@acme.com,Acme,acme.com,do_not_mail\n")
    jane, bob = CsvFinder({"path": str(write(tmp_path, text))}, make_ctx()).find(acme())
    assert jane.email_status == EmailStatus.INVALID and jane.data["csv_email_status"] == "spamtrap"
    assert bob.email_status == EmailStatus.INVALID and bob.data["csv_email_status"] == "do_not_mail"


def test_csv_null_placeholders_are_empty(make_ctx, tmp_path):
    text = ("first name,last name,email,company,website,notes\n"
            "null,N/A,jane@acme.com,Acme Inc,N/A,-\n"
            "Bob,-,bob@beta.com,Beta,-,\n"
            "Cy,Li,cy@gamma.com,Gamma,none,none\n")
    finder = CsvFinder({"path": str(write(tmp_path, text))}, make_ctx())
    (jane,) = finder.find(acme())  # matched through the email domain, not a bogus 'n' domain
    assert (jane.first_name, jane.last_name, jane.full_name) == ("", "", "")
    assert jane.email == "jane@acme.com" and "csv" not in jane.data
    (bob,) = finder.find(Company(name="Beta", domain="beta.com"))
    assert bob.first_name == "Bob" and bob.last_name == ""
    assert [c.first_name for c in finder.find(Company(name="Gamma", domain="gamma.com"))] == ["Cy"]
    assert sorted(finder._by_domain) == ["acme.com", "beta.com", "gamma.com"]


@pytest.mark.parametrize("bad", ["[none]", "acme.com]", "[x]", "http://[::1"])
def test_csv_malformed_website_cell_is_ignored(make_ctx, tmp_path, bad):
    text = ("first name,last name,email,company,website\n"
            "Jane,Doe,jane@acme.com,Acme,acme.com\n"
            f'Bob,Smith,bob@other.com,Other,"{bad}"\n')
    finder = CsvFinder({"path": str(write(tmp_path, text))}, make_ctx())
    assert [c.full_name for c in finder.find(acme())] == ["Jane Doe"]
    assert [c.full_name for c in finder.find(Company(name="Other", domain="other.com"))] == ["Bob Smith"]


def test_csv_full_name_only_drops_honorifics_and_turns_last_first(make_ctx, tmp_path):
    text = ('name,email,company,website\n'
            '"Dr. Jane Doe",jane@acme.com,Acme,acme.com\n'
            '"Doe, John",john@acme.com,Acme,acme.com\n'
            '"Prof. Dr. Hans Meier",hans@acme.com,Acme,acme.com\n'
            'Dr Solo,solo@acme.com,Acme,acme.com\n'
            'Dr.,x@acme.com,Acme,acme.com\n')
    found = CsvFinder({"path": str(write(tmp_path, text))}, make_ctx()).find(acme())
    assert [(c.first_name, c.last_name, c.full_name) for c in found] == [
        ("Jane", "Doe", "Jane Doe"), ("John", "Doe", "John Doe"), ("Hans", "Meier", "Hans Meier"),
        ("", "Solo", "Solo"), ("", "", "")]


# === registry + cross-finder flow ===============================================================

def test_registry_builds_every_finder(make_ctx):
    ctx = make_ctx()
    expected = {"apollo": (ApolloFinder, False), "hunter": (HunterFinder, False),
                "pattern": (PatternFinder, True), "csv": (CsvFinder, True)}
    for type_, (cls, offline) in expected.items():
        finder = registry.create("finder", {"type": type_}, ctx)  # no key / path needed to instantiate
        assert isinstance(finder, cls) and finder.name == type_ and finder.offline is offline


def test_hunter_pattern_hint_feeds_pattern_finder(make_ctx):
    ctx = make_ctx(env={"HUNTER_API_KEY": "h-key"})
    ctx.http.add("GET", HUNTER_DOMAIN, json=hunter_domain_payload(pattern="{first}{l}"))
    company = acme()
    assert HunterFinder({}, ctx).find(company) == []
    person = Contact(first_name="Jane", last_name="Doe", source="apollo")
    PatternFinder({}, ctx).complete(company, person)
    assert person.email_candidates[0] == "janed@acme.com"
    assert re.match(r"^[a-z._]+@acme\.com$", person.email_candidates[-1])
