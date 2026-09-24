"""Tests for the API sources: Apify, Apollo, Adzuna, TheirStack (FakeHttp, no network)."""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any, Dict, List

import pytest

from leadgen import registry
from leadgen.context import MissingCredentialError
from leadgen.http import HttpError
from leadgen.models import SignalType
from leadgen.sources.adzuna import AdzunaSource
from leadgen.sources.apify import ApifySource, guess_preset
from leadgen.sources.apollo import ApolloSource, employee_range
from leadgen.sources.theirstack import TheirStackSource


# =====================================================================================
# Apify
# =====================================================================================

GOOGLE_MAPS_ITEMS: List[Dict[str, Any]] = [
    {
        "title": "Blue Door Dental", "subTitle": None, "price": None, "categoryName": "Dentist",
        "address": "12 High St, Bristol BS1 2AW, United Kingdom", "neighborhood": "Old City",
        "street": "12 High St", "city": "Bristol", "postalCode": "BS1 2AW", "state": None, "countryCode": "GB",
        "website": "https://www.bluedoordental.co.uk/", "phone": "+44 117 496 0000",
        "phoneUnformatted": "+441174960000", "claimThisBusiness": False,
        "location": {"lat": 51.4545, "lng": -2.5879}, "totalScore": 4.8, "permanentlyClosed": False,
        "temporarilyClosed": False, "placeId": "ChIJ2dGMjMMEdkgRqVqkuXQkj7c",
        "categories": ["Dentist", "Cosmetic dentist"], "cid": "13236318428093316777", "reviewsCount": 212,
        "url": "https://www.google.com/maps/search/?api=1&query=Blue%20Door%20Dental&query_place_id=ChIJ2dGMjMMEdkgRqVqkuXQkj7c",
        "emails": ["hello@bluedoordental.co.uk"], "searchString": "dentist bristol", "rank": 1,
    },
    {
        "title": "Harbourside Smiles", "categoryName": "Dental clinic", "address": "4 Quay Rd, Bristol",
        "city": "Bristol", "countryCode": "GB", "website": None, "phone": None, "totalScore": None,
        "reviewsCount": 0, "placeId": "ChIJxyz", "url": "https://www.google.com/maps/place/?q=place_id:ChIJxyz",
        "categories": ["Dental clinic"],
    },
    {"title": "", "address": "nowhere"},  # no name, no website -> skipped
]

LINKEDIN_JOB_ITEMS = [
    {
        "id": "4012345678", "title": "Senior Financial Analyst", "companyName": "Northwind Traders",
        "companyUrl": "https://www.linkedin.com/company/northwind-traders", "companyWebsite": None,
        "location": "Chicago, IL", "postedAt": "2026-09-20", "publishedAt": "2026-09-20T08:00:00.000Z",
        "link": "https://www.linkedin.com/jobs/view/4012345678", "descriptionText": "Own the <b>FP&amp;A</b> cycle.",
        "applicantsCount": "57", "sector": "Retail", "contractType": "Full-time",
        "experienceLevel": "Mid-Senior level", "jobPosterName": "Kim Lee", "jobPosterTitle": "Talent Partner",
        "jobPosterProfileUrl": "https://www.linkedin.com/in/kimlee",
    },
    {
        "id": "4012345679", "title": "AP Specialist", "companyName": "Northwind Traders",
        "companyUrl": "https://www.linkedin.com/company/northwind-traders", "location": "Chicago, IL",
        "postedAt": "2026-09-22", "link": "https://www.linkedin.com/jobs/view/4012345679",
    },
]

INDEED_ITEMS = [
    {
        "positionName": "Bookkeeper", "salary": "$45,000 - $55,000 a year", "jobType": ["Full-time"],
        "company": "Maple Street Bakery", "location": "Portland, OR", "rating": 4.1, "reviewsCount": 12,
        "url": "https://www.indeed.com/viewjob?jk=abc123", "id": "abc123", "postedAt": "30+ days ago",
        "postingDateParsed": "2026-08-20T00:00:00.000Z", "description": "<p>We need a bookkeeper</p>",
        "externalApplyLink": None,
        "companyInfo": {"indeedUrl": "https://www.indeed.com/cmp/Maple-Street-Bakery",
                        "companyDescription": "Neighbourhood bakery", "rating": 4.1},
    },
]

APIFY_RUN_URL = "https://api.apify.com/v2/acts/compass~crawler-google-places/run-sync-get-dataset-items"


def apify(make_ctx, env=None, dry_run=False, **config):
    ctx = make_ctx(env={"APIFY_TOKEN": "apify_tok"} if env is None else env, dry_run=dry_run)
    config.setdefault("type", "apify")
    return ApifySource(config, ctx), ctx


def test_apify_google_maps_run_sync(make_ctx):
    actor_input = {"searchStringsArray": ["dentist"], "locationQuery": "Bristol", "maxCrawledPlacesPerSearch": 50}
    src, ctx = apify(make_ctx, actor="compass/crawler-google-places", input=actor_input, timeout_secs=120)
    ctx.http.add("POST", APIFY_RUN_URL, json=GOOGLE_MAPS_ITEMS)
    companies = src.fetch()

    [call] = ctx.http.calls
    assert call["url"] == APIFY_RUN_URL
    assert call["params"] == {"token": "apify_tok", "format": "json", "clean": "true", "timeout": 120}
    assert call["json"] == actor_input

    assert [c.name for c in companies] == ["Blue Door Dental", "Harbourside Smiles"]
    blue, harbour = companies
    assert blue.domain == "bluedoordental.co.uk" and blue.industry == "Dentist"
    assert blue.location == "12 High St, Bristol BS1 2AW, United Kingdom" and blue.country == "GB"
    assert blue.keywords == ["Dentist", "Cosmetic dentist"]
    assert blue.data["phone"] == "+44 117 496 0000" and blue.data["rating"] == 4.8
    assert blue.data["city"] == "Bristol" and blue.sources == ["apify"]
    [sig] = blue.signals
    assert sig.type == SignalType.REVIEW and sig.title == "Rated 4.8 from 212 Google reviews"
    assert sig.external_id == "ChIJ2dGMjMMEdkgRqVqkuXQkj7c" and sig.url.startswith("https://www.google.com/maps")
    assert sig.data == {"rating": 4.8, "reviews_count": 212} and sig.source == "apify"
    [contact] = blue.contacts
    assert contact.email == "hello@bluedoordental.co.uk" and contact.phone == "+44 117 496 0000"
    # no rating -> no review signal (preset's signal_requires), and no website -> no domain
    assert harbour.signals == [] and harbour.domain == "" and harbour.contacts == []


def test_apify_linkedin_jobs_preset_guessed_from_actor(make_ctx):
    src, ctx = apify(make_ctx, actor="bebity/linkedin-jobs-scraper", input={"title": "financial analyst"})
    ctx.http.add("POST", "https://api.apify.com/v2/acts/bebity~linkedin-jobs-scraper/run-sync-get-dataset-items",
                 json=LINKEDIN_JOB_ITEMS)
    [c] = src.fetch()
    assert src.preset == "linkedin_jobs"
    assert c.name == "Northwind Traders" and c.domain == ""  # LinkedIn URL is never a company domain
    assert c.linkedin_url == "https://www.linkedin.com/company/northwind-traders"
    assert c.location == "Chicago, IL" and c.industry == "Retail"
    s1, s2 = c.signals
    assert s1.type == SignalType.JOB_POSTING and s1.title == "Senior Financial Analyst"
    assert s1.posted_at == date(2026, 9, 20) and s1.external_id == "4012345678"
    assert s1.url == "https://www.linkedin.com/jobs/view/4012345678"
    assert s1.description == "Own the FP&A cycle."
    assert s1.data["seniority"] == "Mid-Senior level" and s1.data["employment_type"] == "Full-time"
    assert s2.posted_at == date(2026, 9, 22)
    [poster] = c.contacts
    assert poster.full_name == "Kim Lee" and poster.title == "Talent Partner"


def test_apify_indeed_dataset_mode(make_ctx):
    src, ctx = apify(make_ctx, dataset_id="WkzbQMuFYuamGv3YF", preset="indeed_jobs", max_items=500)
    ctx.http.add("GET", "https://api.apify.com/v2/datasets/WkzbQMuFYuamGv3YF/items", json=INDEED_ITEMS)
    [c] = src.fetch()
    [call] = ctx.http.calls
    assert call["method"] == "GET"
    assert call["params"] == {"token": "apify_tok", "format": "json", "clean": "true", "limit": 500}
    assert c.name == "Maple Street Bakery" and c.description == "Neighbourhood bakery"
    [s] = c.signals
    assert s.title == "Bookkeeper" and s.posted_at == date(2026, 8, 20) and s.external_id == "abc123"
    assert s.description == "We need a bookkeeper" and s.data["salary"] == "$45,000 - $55,000 a year"
    assert c.data["indeed_company_url"] == "https://www.indeed.com/cmp/Maple-Street-Bakery"


def test_apify_last_run_and_custom_mapping_without_preset(make_ctx):
    items = [{"org": {"name": "Pixel Forge", "site": "pixelforge.io"}, "ad": {"headline": "Spring sale", "seen": "2026-09-19"}},
             {"org": {"name": "Pixel Forge"}, "ad": {"headline": "Wishlist now"}}]
    src, ctx = apify(make_ctx, actor="someone/ad-library-scraper", use_last_run=True, preset="none",
                     signal_type="ad_activity",
                     mapping={"name": "org.name", "website": "org.site", "signal_title": "ad.headline",
                              "signal_date": "ad.seen"})
    ctx.http.add("GET", "https://api.apify.com/v2/acts/someone~ad-library-scraper/runs/last/dataset/items",
                 json=items)
    [c] = src.fetch()
    assert ctx.http.calls[0]["params"]["status"] == "SUCCEEDED"
    assert c.domain == "pixelforge.io" and [s.title for s in c.signals] == ["Spring sale", "Wishlist now"]
    assert all(s.type == "ad_activity" for s in c.signals)


def test_apify_auto_detects_flat_items_without_preset(make_ctx):
    items = [{"companyName": "Kestrel Logistics", "website": "kestrel.fr", "title": "Warehouse Lead",
              "postedAt": "1 week ago", "url": "https://jobs.example/k1"}]
    src, ctx = apify(make_ctx, actor="someone/generic-jobs", preset=None)
    ctx.http.add("POST", re.compile(r"/acts/someone~generic-jobs/run-sync-get-dataset-items$"), json=items)
    [c] = src.fetch()
    assert c.name == "Kestrel Logistics" and c.signals[0].type == SignalType.JOB_POSTING
    assert c.signals[0].posted_at == date(2026, 9, 17)


def test_apify_mapping_override_and_template_disable(make_ctx):
    src, ctx = apify(make_ctx, actor="compass/crawler-google-places", signal_title_template=None,
                     mapping={"industry": "categories.1"},
                     default_signal={"type": "custom", "title": "Local business in {city}"})
    ctx.http.add("POST", APIFY_RUN_URL, json=GOOGLE_MAPS_ITEMS[:1])
    [c] = src.fetch()
    assert c.industry == "Cosmetic dentist"
    assert [s.title for s in c.signals] == ["Local business in Bristol"]


def test_apify_limit(make_ctx):
    items = [{"title": f"Shop {i}", "website": f"shop{i}.com", "totalScore": 4, "reviewsCount": i} for i in range(10)]
    src, ctx = apify(make_ctx, actor="compass/crawler-google-places", limit=3)
    ctx.http.add("POST", APIFY_RUN_URL, json=items)
    assert [c.name for c in src.fetch()] == ["Shop 0", "Shop 1", "Shop 2"]


def test_apify_errors(make_ctx):
    src, ctx = apify(make_ctx, actor="compass/crawler-google-places")
    ctx.http.add("POST", APIFY_RUN_URL, status=400,
                 json={"error": {"type": "run-failed", "message": "Actor run did not succeed"}})
    with pytest.raises(HttpError) as ei:
        src.fetch()
    assert ei.value.status == 400 and "apify_tok" not in str(ei.value)

    src, ctx = apify(make_ctx, actor="compass/crawler-google-places")
    ctx.http.add("POST", APIFY_RUN_URL, json={"error": {"type": "invalid-input", "message": "Bad input"}})
    with pytest.raises(ValueError, match="Bad input"):
        src.fetch()

    src, ctx = apify(make_ctx, actor="compass/crawler-google-places", input=["not", "a", "dict"])
    with pytest.raises(ValueError, match="'input' must be a mapping"):
        src.fetch()

    src, ctx = apify(make_ctx)
    with pytest.raises(ValueError, match="'actor'"):
        src.fetch()

    src, ctx = apify(make_ctx, actor="x/y", preset="tiktok")
    with pytest.raises(ValueError, match="unknown preset"):
        src.fetch()


def test_apify_empty_and_wrapped_payloads(make_ctx, caplog):
    src, ctx = apify(make_ctx, dataset_id="d1")
    ctx.http.add("GET", "https://api.apify.com/v2/datasets/d1/items", json=[])
    with caplog.at_level(logging.WARNING):
        assert src.fetch() == []
    assert "no items" in caplog.text
    src, ctx = apify(make_ctx, dataset_id="d2", preset="google_maps")
    ctx.http.add("GET", "https://api.apify.com/v2/datasets/d2/items", json={"items": GOOGLE_MAPS_ITEMS[:1]})
    assert [c.name for c in src.fetch()] == ["Blue Door Dental"]


def test_apify_missing_token_is_lazy(make_ctx):
    src, ctx = apify(make_ctx, env={}, actor="compass/crawler-google-places")  # no error on construction
    with pytest.raises(MissingCredentialError, match="APIFY_TOKEN"):
        src.fetch()
    assert ctx.http.calls == []
    src, ctx = apify(make_ctx, env={}, actor="compass/crawler-google-places", api_key="cfg_tok")
    ctx.http.add("POST", APIFY_RUN_URL, json=[])
    src.fetch()
    assert ctx.http.calls[0]["params"]["token"] == "cfg_tok"


def test_apify_dry_run_makes_no_calls(make_ctx):
    src, ctx = apify(make_ctx, dry_run=True, actor="compass/crawler-google-places")
    assert src.fetch() == [] and ctx.http.calls == []
    assert src.offline is False and src.env_key == "APIFY_TOKEN"


def test_guess_preset():
    assert guess_preset("compass/crawler-google-places") == "google_maps"
    assert guess_preset("curious_coder/linkedin-jobs-scraper") == "linkedin_jobs"
    assert guess_preset("misceres/indeed-scraper") == "indeed_jobs"
    assert guess_preset("apify/web-scraper") == ""


# =====================================================================================
# Apollo
# =====================================================================================

APOLLO_SEARCH = "https://api.apollo.io/api/v1/mixed_companies/search"

ACME_ORG = {
    "id": "5e66b6381e05b4008c8331b8", "name": "Acme Robotics", "website_url": "http://www.acmerobotics.com",
    "blog_url": None, "angellist_url": None, "linkedin_url": "http://www.linkedin.com/company/acme-robotics",
    "twitter_url": None, "facebook_url": None,
    "primary_phone": {"number": "+1 555-0100", "source": "Account", "sanitized_number": "+15550100"},
    "languages": [], "alexa_ranking": 912345, "phone": "+1 555-0100", "linkedin_uid": "123456",
    "founded_year": 2015, "publicly_traded_symbol": None, "publicly_traded_exchange": None,
    "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/acme.png",
    "crunchbase_url": None, "primary_domain": "acmerobotics.com", "sanitized_phone": "+15550100",
    "estimated_num_employees": 120, "industry": "machinery", "keywords": ["robotics", "automation"],
    "city": "Denver", "state": "Colorado", "country": "United States",
    "short_description": "Acme builds <b>warehouse</b> robots.",
    "latest_funding_stage": "Series A", "latest_funding_round_date": "2026-08-01T00:00:00.000+00:00",
    "total_funding": 18000000, "total_funding_printed": "18M",
    "funding_events": [
        {"id": "fe1", "date": "2026-08-01T00:00:00.000+00:00", "news_url": "https://news.example/acme-a",
         "type": "Series A", "investors": "Big VC", "amount": "12M", "currency": "$"},
        {"id": "fe0", "date": "2024-03-01T00:00:00.000+00:00", "news_url": None, "type": "Seed",
         "investors": "Angels", "amount": "6M", "currency": "$"},
    ],
    "organization_headcount_six_month_growth": 0.18, "organization_headcount_twelve_month_growth": 0.35,
    "organization_headcount_twenty_four_month_growth": 0.6,
}
BETA_ORG = {
    "id": "6011aa0a1f1a2b000135bcd1", "name": "Beta Analytics", "website_url": None,
    "primary_domain": "betaanalytics.io", "linkedin_url": None, "estimated_num_employees": 45,
    "industry": "information technology & services", "keywords": [], "city": "Austin", "state": "Texas",
    "country": "United States", "latest_funding_stage": None, "latest_funding_round_date": None,
    "organization_headcount_six_month_growth": 0.02, "organization_headcount_twelve_month_growth": 0.12,
}
GAMMA_ACCOUNT = {
    "id": "64b0c0ffee0000000000acc1", "organization_id": "5f2a0000000000000000org3", "name": "Gamma Freight",
    "website_url": "http://www.gammafreight.com", "primary_domain": "gammafreight.com",
    "estimated_num_employees": 300, "city": "Rotterdam", "country": "Netherlands",
    "latest_funding_stage": "series_b", "latest_funding_round_date": "2026-09-01",
}
DELTA_ORG = {"id": "70000000000000000000dddd", "name": "Delta Foods", "primary_domain": "deltafoods.com",
             "estimated_num_employees": 60, "country": "Canada"}


def apollo_page(page: int, orgs: List[Dict[str, Any]], accounts=None, total_pages: int = 2) -> Dict[str, Any]:
    return {
        "breadcrumbs": [{"label": "Company Locations", "signal_field_name": "organization_locations",
                         "value": "united states", "display_name": "united states"}],
        "partial_results_only": False, "disable_eu_prospecting": False, "partial_results_limit": 10000,
        "pagination": {"page": page, "per_page": 3, "total_entries": 4, "total_pages": total_pages},
        "accounts": accounts or [], "organizations": orgs,
    }


def apollo(make_ctx, env=None, dry_run=False, icp=None, **config):
    kwargs: Dict[str, Any] = {}
    if icp is not None:
        kwargs["icp"] = icp
    ctx = make_ctx(env={"APOLLO_API_KEY": "apollo_key"} if env is None else env, dry_run=dry_run, **kwargs)
    config.setdefault("type", "apollo")
    return ApolloSource(config, ctx), ctx


def test_apollo_search_request_shape_paging_and_mapping(make_ctx):
    src, ctx = apollo(make_ctx, locations=["United States", "Netherlands"],
                      employee_ranges=["11,50", "51-200", [201, 500]], keywords=["logistics", "robotics"],
                      filters={"organization_not_locations": ["texas"]}, per_page=3, max_pages=5)
    ctx.http.add("POST", APOLLO_SEARCH, json=apollo_page(1, [ACME_ORG, BETA_ORG], [GAMMA_ACCOUNT]), times=1)
    ctx.http.add("POST", APOLLO_SEARCH, json=apollo_page(2, [DELTA_ORG]), times=1)
    companies = src.fetch()

    assert len(ctx.http.calls) == 2  # stopped at total_pages
    first = ctx.http.calls[0]
    assert first["headers"]["x-api-key"] == "apollo_key"
    assert first["headers"]["Content-Type"] == "application/json"
    assert first["json"] == {
        "organization_locations": ["United States", "Netherlands"],
        "organization_num_employees_ranges": ["11,50", "51,200", "201,500"],
        "q_organization_keyword_tags": ["logistics", "robotics"],
        "organization_not_locations": ["texas"], "page": 1, "per_page": 3,
    }
    assert ctx.http.calls[1]["json"]["page"] == 2

    assert [c.name for c in companies] == ["Acme Robotics", "Beta Analytics", "Gamma Freight", "Delta Foods"]
    acme, beta, gamma, delta = companies
    assert acme.domain == "acmerobotics.com" and acme.website == "http://www.acmerobotics.com"
    assert acme.linkedin_url == "http://www.linkedin.com/company/acme-robotics"
    assert acme.employees == 120 and acme.industry == "machinery" and acme.keywords == ["robotics", "automation"]
    assert acme.location == "Denver, Colorado, United States" and acme.country == "United States"
    assert acme.description == "Acme builds warehouse robots."
    assert acme.data["apollo_id"] == "5e66b6381e05b4008c8331b8" and acme.data["phone"] == "+1 555-0100"
    assert acme.sources == ["apollo"]
    [fund] = acme.signals  # no growth signal: headcount_growth_min not set
    assert fund.type == SignalType.FUNDING and fund.title == "Raised Series A ($12M)"
    assert fund.posted_at == date(2026, 8, 1) and fund.url == "https://news.example/acme-a"
    assert fund.description == "Total funding to date: $18M" and fund.source == "apollo"

    assert beta.domain == "betaanalytics.io" and beta.signals == []
    # accounts carry the Apollo organization id in organization_id
    assert gamma.data["apollo_id"] == "5f2a0000000000000000org3"
    assert gamma.signals[0].title == "Raised Series B" and gamma.signals[0].posted_at == date(2026, 9, 1)
    assert delta.country == "Canada"


def test_apollo_limit_sets_page_size_and_stops(make_ctx):
    src, ctx = apollo(make_ctx, limit=2, locations=["germany"])
    ctx.http.add("POST", APOLLO_SEARCH, json=apollo_page(1, [ACME_ORG, BETA_ORG, DELTA_ORG], total_pages=9))
    companies = src.fetch()
    assert len(ctx.http.calls) == 1 and ctx.http.calls[0]["json"]["per_page"] == 2
    assert [c.name for c in companies] == ["Acme Robotics", "Beta Analytics"]


def test_apollo_max_pages_and_empty_page(make_ctx):
    src, ctx = apollo(make_ctx, max_pages=3, locations=["x"])
    ctx.http.add("POST", APOLLO_SEARCH, json=apollo_page(1, [ACME_ORG], total_pages=10), times=1)
    ctx.http.add("POST", APOLLO_SEARCH, json=apollo_page(2, [], total_pages=10), times=1)
    assert [c.name for c in src.fetch()] == ["Acme Robotics"]
    assert len(ctx.http.calls) == 2  # empty page ends paging
    src, ctx = apollo(make_ctx, max_pages=2, locations=["x"])
    ctx.http.add("POST", APOLLO_SEARCH, fn=lambda call: apollo_page(call["json"]["page"], [
        {"id": f"o{call['json']['page']}", "name": f"Co {call['json']['page']}"}], total_pages=10))
    assert len(src.fetch()) == 2 and len(ctx.http.calls) == 2


def test_apollo_headcount_growth_signal(make_ctx):
    src, ctx = apollo(make_ctx, headcount_growth_min=0.1, locations=["x"])
    ctx.http.add("POST", APOLLO_SEARCH, json=apollo_page(1, [ACME_ORG, BETA_ORG, DELTA_ORG], total_pages=1))
    acme, beta, delta = src.fetch()
    growth = [s for s in acme.signals if s.type == SignalType.HEADCOUNT_GROWTH]
    assert [s.title for s in growth] == ["Headcount up 18% in 6 months"]
    assert growth[0].data["twelve_month_growth"] == 0.35
    assert [s.title for s in beta.signals] == ["Headcount up 12% in 12 months"]  # falls back to 12 months
    assert delta.signals == []
    # percentages are accepted too
    src, ctx = apollo(make_ctx, headcount_growth_min=20, locations=["x"])
    ctx.http.add("POST", APOLLO_SEARCH, json=apollo_page(1, [ACME_ORG], total_pages=1))
    [acme] = src.fetch()
    assert [s.title for s in acme.signals if s.type == SignalType.HEADCOUNT_GROWTH] == [
        "Headcount up 35% in 12 months"]


def test_apollo_job_postings(make_ctx, caplog):
    src, ctx = apollo(make_ctx, job_postings=True, job_postings_per_page=10, locations=["x"])
    ctx.http.add("POST", APOLLO_SEARCH, json=apollo_page(1, [ACME_ORG, BETA_ORG], total_pages=1))
    ctx.http.add("GET", "https://api.apollo.io/api/v1/organizations/5e66b6381e05b4008c8331b8/job_postings", json={
        "organization_job_postings": [
            {"id": "65f0000000000000000000j1", "title": "Controller", "url": "https://acme.jobs/controller",
             "city": "Denver", "state": "Colorado", "country": "United States",
             "last_seen_at": "2026-09-23T00:00:00.000+00:00", "posted_at": "2026-09-10T00:00:00.000+00:00"},
            {"id": "65f0000000000000000000j2", "title": "", "url": None},
            {"id": "65f0000000000000000000j3", "title": "Senior Accountant", "url": None, "city": None,
             "state": None, "country": "United States", "posted_at": None},
        ],
        "pagination": {"page": 1, "per_page": 10, "total_entries": 3, "total_pages": 1},
    })
    ctx.http.add("GET", "https://api.apollo.io/api/v1/organizations/6011aa0a1f1a2b000135bcd1/job_postings",
                 text="<html>502 Bad Gateway</html>")
    with caplog.at_level(logging.WARNING):
        acme, beta = src.fetch()
    jobs_call = ctx.http.calls_to("/job_postings")[0]
    assert jobs_call["params"] == {"page": 1, "per_page": 10}
    assert jobs_call["headers"]["x-api-key"] == "apollo_key"
    jobs = [s for s in acme.signals if s.type == SignalType.JOB_POSTING]
    assert [s.title for s in jobs] == ["Controller", "Senior Accountant"]
    assert jobs[0].posted_at == date(2026, 9, 10) and jobs[0].location == "Denver, Colorado, United States"
    assert jobs[0].external_id == "65f0000000000000000000j1" and jobs[0].data["last_seen_at"].startswith("2026-09-23")
    assert jobs[1].posted_at is None
    assert beta.signals == [] and "job postings for Beta Analytics failed" in caplog.text


def test_apollo_uses_icp_hints_unless_disabled(make_ctx, caplog):
    icp = {"locations": ["Germany", "Austria"], "employees": {"min": 20, "max": 250}}
    src, ctx = apollo(make_ctx, icp=icp)
    ctx.http.add("POST", APOLLO_SEARCH, json=apollo_page(1, [], total_pages=1))
    assert src.fetch() == []
    body = ctx.http.calls[0]["json"]
    assert body["organization_locations"] == ["Germany", "Austria"]
    assert body["organization_num_employees_ranges"] == ["20,250"]

    src, ctx = apollo(make_ctx, icp=icp, use_icp=False)
    ctx.http.add("POST", APOLLO_SEARCH, json=apollo_page(1, [], total_pages=1))
    with caplog.at_level(logging.WARNING):
        src.fetch()
    body = ctx.http.calls[0]["json"]
    assert "organization_locations" not in body and "organization_num_employees_ranges" not in body
    assert "no search filters" in caplog.text


def test_apollo_http_errors(make_ctx, caplog):
    src, ctx = apollo(make_ctx, locations=["x"])
    ctx.http.add("POST", APOLLO_SEARCH, status=401, json={"error": "Invalid access credentials."})
    with pytest.raises(HttpError) as ei:
        src.fetch()
    assert ei.value.status == 401

    src, ctx = apollo(make_ctx, locations=["x"], max_pages=3)
    ctx.http.add("POST", APOLLO_SEARCH, json=apollo_page(1, [ACME_ORG], total_pages=3), times=1)
    ctx.http.add("POST", APOLLO_SEARCH, status=500, json={"error": "boom"})
    with caplog.at_level(logging.WARNING):
        assert [c.name for c in src.fetch()] == ["Acme Robotics"]
    assert "page 2 failed" in caplog.text

    src, ctx = apollo(make_ctx, locations=["x"])
    ctx.http.add("POST", APOLLO_SEARCH, text="[]")
    with pytest.raises(ValueError, match="unexpected Apollo response"):
        src.fetch()


def test_apollo_malformed_records_are_tolerated(make_ctx):
    src, ctx = apollo(make_ctx, locations=["x"])
    ctx.http.add("POST", APOLLO_SEARCH, json={"organizations": [None, "junk", {"id": "o1"},
                                                                 {"id": "o2", "name": "Only Name"}],
                                              "pagination": None})
    [c] = src.fetch()
    assert c.name == "Only Name" and c.data["apollo_id"] == "o2" and c.signals == []


def test_apollo_missing_key_lazy_and_dry_run(make_ctx):
    src, ctx = apollo(make_ctx, env={}, locations=["x"])
    with pytest.raises(MissingCredentialError, match="APOLLO_API_KEY"):
        src.fetch()
    src, ctx = apollo(make_ctx, dry_run=True, locations=["x"])
    assert src.fetch() == [] and ctx.http.calls == []


def test_apollo_bad_config_values(make_ctx):
    src, ctx = apollo(make_ctx, employee_ranges=["lots"])
    with pytest.raises(ValueError, match="employee range"):
        src.fetch()
    src, ctx = apollo(make_ctx, locations=["x"], filters=["nope"])
    with pytest.raises(ValueError, match="'filters' must be a mapping"):
        src.fetch()
    src, ctx = apollo(make_ctx, locations=["x"], per_page="many")
    with pytest.raises(ValueError, match="'per_page' must be an integer"):
        src.fetch()


def test_employee_range_normalisation():
    assert employee_range("11,50") == "11,50"
    assert employee_range("51-200") == "51,200"
    assert employee_range("1,001-5,000") == "1001,5000" and employee_range("1001 - 5000") == "1001,5000"
    with pytest.raises(ValueError):
        employee_range("1,000,5000")
    assert employee_range([201, 500]) == "201,500"
    assert employee_range({"min": 10, "max": None}) == "10,10000000"
    assert employee_range({"min": None, "max": 50}) == "1,50"
    assert employee_range("500+") == "500,10000000"
    assert employee_range("200,50") == "50,200"
    assert employee_range(None) is None and employee_range({"min": None, "max": None}) is None


def test_apollo_mapping_override(make_ctx):
    src, ctx = apollo(make_ctx, locations=["x"], mapping={"industry": "keywords.0", "description": None})
    ctx.http.add("POST", APOLLO_SEARCH, json=apollo_page(1, [ACME_ORG], total_pages=1))
    [c] = src.fetch()
    assert c.industry == "robotics" and c.description == ""


# =====================================================================================
# Adzuna
# =====================================================================================

def adzuna_job(job_id: str, title: str, company: str, created: str = "2026-09-20T09:15:00Z",
               location: str = "Manchester, Greater Manchester") -> Dict[str, Any]:
    return {
        "__CLASS__": "Adzuna::API::Response::Job", "id": job_id, "adref": "eyJhbGciOiJIUzI1NiJ9.eyJzIjoiMSJ9",
        "title": title, "description": f"We are looking for a <strong>{title}</strong> to join our team&hellip;",
        "created": created,
        "redirect_url": f"https://www.adzuna.co.uk/jobs/land/ad/{job_id}?se=abc&utm_medium=api&v=DEF",
        "company": {"__CLASS__": "Adzuna::API::Response::Company", "display_name": company},
        "location": {"__CLASS__": "Adzuna::API::Response::Location", "display_name": location,
                     "area": ["UK", "North West England", "Greater Manchester", "Manchester"]},
        "category": {"__CLASS__": "Adzuna::API::Response::Category", "label": "Accounting & Finance Jobs",
                     "tag": "accounting-finance-jobs"},
        "salary_min": 45000, "salary_max": 55000, "salary_is_predicted": "0",
        "contract_type": "permanent", "contract_time": "full_time", "latitude": 53.48, "longitude": -2.24,
    }


def adzuna_page(results: List[Dict[str, Any]], count: int = 100) -> Dict[str, Any]:
    return {"__CLASS__": "Adzuna::API::Response::JobSearchResults", "count": count, "mean": 51234.5,
            "results": results}


def adzuna(make_ctx, env=None, dry_run=False, **config):
    ctx = make_ctx(env={"ADZUNA_APP_ID": "app123", "ADZUNA_APP_KEY": "key456"} if env is None else env,
                   dry_run=dry_run)
    config.setdefault("type", "adzuna")
    return AdzunaSource(config, ctx), ctx


def test_adzuna_request_shape_and_grouping(make_ctx):
    src, ctx = adzuna(make_ctx, countries=["gb"], queries=["accountant"], what_exclude=["trainee", "intern"],
                      where="Manchester", max_days_old=14, results_per_page=3, max_pages=1, category="accounting-finance-jobs")
    ctx.http.add("GET", re.compile(r"/jobs/gb/search/1$"), json=adzuna_page([
        adzuna_job("4312345678", "Senior <strong>Accountant</strong>", "Harbor Freight Partners"),
        adzuna_job("4312345679", "Management Accountant", "Harbor Freight Partners", created="2026-09-18T08:00:00Z"),
        adzuna_job("4312345680", "Accountant", "", created="2026-09-18T08:00:00Z"),
    ]))
    companies = src.fetch()
    [call] = ctx.http.calls
    assert call["url"] == "https://api.adzuna.com/v1/api/jobs/gb/search/1"
    assert call["params"] == {
        "app_id": "app123", "app_key": "key456", "results_per_page": 3, "max_days_old": 14, "sort_by": "date",
        "content-type": "application/json", "what": "accountant", "what_exclude": "trainee intern",
        "where": "Manchester", "category": "accounting-finance-jobs",
    }
    [c] = companies  # the job without an employer is skipped
    assert c.name == "Harbor Freight Partners" and c.country == "GB"
    assert c.location == "Manchester, Greater Manchester" and c.sources == ["adzuna"]
    s1, s2 = c.signals
    assert s1.type == SignalType.JOB_POSTING and s1.title == "Senior Accountant"
    assert s1.description == "We are looking for a Senior Accountant to join our team…"
    assert s1.posted_at == date(2026, 9, 20) and s1.external_id == "4312345678"
    assert s1.url.startswith("https://www.adzuna.co.uk/jobs/land/ad/4312345678")
    assert s1.location == "Manchester, Greater Manchester" and s1.source == "adzuna"
    assert s1.data["salary_max"] == 55000 and s1.data["category"] == "Accounting & Finance Jobs"
    assert s1.data["contract_time"] == "full_time"


def test_adzuna_countries_queries_and_paging(make_ctx):
    src, ctx = adzuna(make_ctx, countries=["uk", "de"], queries=["buchhalter", "controller"], results_per_page=2,
                      max_pages=3)

    def respond(call):
        m = re.search(r"/jobs/(\w+)/search/(\d+)$", call["url"])
        country, page = m.group(1), int(m.group(2))
        what = call["params"]["what"]
        if page == 1:
            return adzuna_page([adzuna_job(f"{country}{what}1", f"{what} 1", f"{country.upper()} Co A"),
                                adzuna_job(f"{country}{what}2", f"{what} 2", f"{country.upper()} Co B")], count=3)
        return adzuna_page([adzuna_job(f"{country}{what}3", f"{what} 3", f"{country.upper()} Co A")], count=3)

    ctx.http.add("GET", re.compile(r"api\.adzuna\.com/v1/api/jobs/"), fn=respond)
    companies = src.fetch()
    urls = [c["url"].split("/jobs/")[1] for c in ctx.http.calls]
    assert urls == ["gb/search/1", "gb/search/2", "gb/search/1", "gb/search/2",
                    "de/search/1", "de/search/2", "de/search/1", "de/search/2"]
    assert [c.name for c in companies] == ["GB Co A", "GB Co B", "DE Co A", "DE Co B"]
    assert companies[0].country == "GB" and companies[2].country == "DE"
    assert len(companies[0].signals) == 4  # 2 queries x (page 1 + page 2)


def test_adzuna_limit_stops_early(make_ctx):
    src, ctx = adzuna(make_ctx, countries="gb", queries=["a", "b"], results_per_page=2, max_pages=5, limit=1)
    ctx.http.add("GET", re.compile(r"/jobs/gb/search/\d+$"),
                 json=adzuna_page([adzuna_job("1", "Role", "Only Co"), adzuna_job("2", "Other", "Second Co")]))
    companies = src.fetch()
    assert [c.name for c in companies] == ["Only Co"] and len(ctx.http.calls) == 1


def test_adzuna_errors(make_ctx, caplog):
    src, ctx = adzuna(make_ctx, countries=["gb"], queries=["a"])
    ctx.http.add("GET", re.compile(r"/search/1$"), status=401, json={"exception": "AUTH_FAIL"})
    with pytest.raises(HttpError):
        src.fetch()

    src, ctx = adzuna(make_ctx, countries=["gb"], queries=["broken", "ok"], max_pages=1)
    ctx.http.add("GET", re.compile(r"/search/1$"),
                 fn=lambda call: (500, {"exception": "boom"}) if call["params"]["what"] == "broken"
                 else adzuna_page([adzuna_job("1", "Role", "Good Co")]))
    with caplog.at_level(logging.WARNING):
        [c] = src.fetch()
    assert c.name == "Good Co" and "failed" in caplog.text

    src, ctx = adzuna(make_ctx, countries=["gb"], queries=["a", "b"], max_pages=1)
    ctx.http.add("GET", re.compile(r"/search/1$"), status=503, json={"exception": "down"})
    with pytest.raises(HttpError):
        src.fetch()  # every search failed -> surfaced

    src, ctx = adzuna(make_ctx, countries=["gb"], queries=["html", "ok"], max_pages=1)
    ctx.http.add("GET", re.compile(r"/search/1$"),
                 fn=lambda call: "<html>oops</html>" if call["params"]["what"] == "html"
                 else adzuna_page([adzuna_job("1", "Role", "Good Co")]))
    assert [c.name for c in src.fetch()] == ["Good Co"]


def test_adzuna_defaults_and_malformed_payloads(make_ctx):
    src, ctx = adzuna(make_ctx)
    ctx.http.add("GET", re.compile(r"/jobs/us/search/1$"),
                 json={"results": [None, "junk", {"title": "x"}, {"company": {"display_name": "Solo LLC"}}]})
    [c] = src.fetch()
    call = ctx.http.calls[0]
    assert "what" not in call["params"] and call["params"]["max_days_old"] == 30
    assert call["params"]["results_per_page"] == 50
    assert c.name == "Solo LLC" and c.signals == [] and c.country == "US"
    assert len(ctx.http.calls) == 1  # fewer results than a page -> no page 2


def test_adzuna_credentials(make_ctx):
    src, ctx = adzuna(make_ctx, env={"ADZUNA_APP_ID": "only-id"})
    with pytest.raises(MissingCredentialError, match="ADZUNA_APP_KEY"):
        src.fetch()
    src, ctx = adzuna(make_ctx, env={}, app_id="cfg-id", app_key="cfg-key", max_pages=1)
    ctx.http.add("GET", re.compile(r"/search/1$"), json=adzuna_page([]))
    src.fetch()
    assert ctx.http.calls[0]["params"]["app_id"] == "cfg-id" and ctx.http.calls[0]["params"]["app_key"] == "cfg-key"
    src, ctx = adzuna(make_ctx, dry_run=True)
    assert src.fetch() == [] and ctx.http.calls == []


# =====================================================================================
# TheirStack
# =====================================================================================

THEIRSTACK_URL = "https://api.theirstack.com/v1/jobs/search"


def ts_job(job_id: int, title: str, company: str = "Nordlicht GmbH", domain: str = "nordlicht.de",
           hiring_team=None, date_posted: str = "2026-09-18") -> Dict[str, Any]:
    return {
        "id": job_id, "job_title": title, "url": f"https://www.linkedin.com/jobs/view/{job_id}",
        "final_url": f"https://boards.greenhouse.io/nordlicht/jobs/{job_id}", "source_url": None,
        "date_posted": date_posted, "discovered_at": f"{date_posted}T10:00:00", "reposted": False,
        "reposted_date": None, "location": "Berlin, Germany", "short_location": "Berlin",
        "long_location": "Berlin, Berlin, Germany", "state_code": None, "postal_code": None,
        "latitude": 52.52, "longitude": 13.4, "country": "Germany", "country_code": "DE",
        "country_codes": ["DE"], "cities": ["Berlin"], "remote": False, "hybrid": True,
        "salary_string": "€90k-€110k", "min_annual_salary_usd": 97000, "max_annual_salary_usd": 119000,
        "seniority": "senior", "employment_statuses": ["full_time"], "easy_apply": False,
        "description": "<p>We are hiring a <b>" + title + "</b>.</p>", "company": company,
        "company_domain": domain,
        "company_object": {
            "id": "nordlicht", "name": company, "domain": domain, "industry": "Software Development",
            "country": "Germany", "country_code": "DE", "employee_count": 85,
            "linkedin_url": "https://www.linkedin.com/company/nordlicht", "long_description": "Nordlicht builds fleet software.",
            "city": "Berlin", "logo": "https://media.licdn.com/logo.png", "founded_year": 2016,
        },
        "hiring_team": hiring_team if hiring_team is not None else [
            {"full_name": "Lena Vogel", "first_name": "Lena", "linkedin_url": "https://www.linkedin.com/in/lenavogel",
             "role": "CFO", "image_url": "https://media.licdn.com/lena.png", "thumbnail_url": None},
        ],
    }


def ts_page(jobs: List[Dict[str, Any]], total: int) -> Dict[str, Any]:
    return {"metadata": {"total_results": total, "truncated_results": 0, "truncated_companies": 0,
                         "total_companies": 2}, "data": jobs}


def theirstack(make_ctx, env=None, dry_run=False, icp=None, **config):
    kwargs: Dict[str, Any] = {}
    if icp is not None:
        kwargs["icp"] = icp
    ctx = make_ctx(env={"THEIRSTACK_API_KEY": "ts_key"} if env is None else env, dry_run=dry_run, **kwargs)
    config.setdefault("type", "theirstack")
    return TheirStackSource(config, ctx), ctx


def test_theirstack_request_shape_and_mapping(make_ctx):
    src, ctx = theirstack(make_ctx, job_titles=["Head of Finance", "CFO"], exclude_job_titles=["intern"],
                          countries=["de", "at"], max_age_days=14, min_employees=20, max_employees=500,
                          filters={"job_description_pattern_or": ["SAP"]}, limit=2, max_pages=2)
    other = ts_job(2, "Controller", company="Alpenwerk AG", domain="alpenwerk.at", hiring_team=[])
    other["company_object"].update({"name": "Alpenwerk AG", "domain": "alpenwerk.at", "country": "Austria",
                                    "city": "Vienna", "id": "alpenwerk"})
    ctx.http.add("POST", THEIRSTACK_URL, json=ts_page([ts_job(1, "Head of Finance"), other], total=3), times=1)
    ctx.http.add("POST", THEIRSTACK_URL, json=ts_page([ts_job(3, "Senior Accountant", hiring_team=[
        {"full_name": "Lena Vogel", "linkedin_url": "https://www.linkedin.com/in/lenavogel", "role": "CFO"},
        {"full_name": "Jonas Weber", "role": "Finance Director"}])], total=3), times=1)
    companies = src.fetch()

    first, second = ctx.http.calls
    assert first["headers"]["Authorization"] == "Bearer ts_key"
    assert first["json"] == {
        "posted_at_max_age_days": 14, "job_title_or": ["Head of Finance", "CFO"], "job_title_not": ["intern"],
        "job_country_code_or": ["DE", "AT"], "min_employee_count": 20, "max_employee_count": 500,
        "job_description_pattern_or": ["SAP"], "page": 0, "limit": 2,
    }
    assert second["json"]["page"] == 1

    nord, alpen = companies
    assert nord.name == "Nordlicht GmbH" and nord.domain == "nordlicht.de" and nord.employees == 85
    assert nord.industry == "Software Development" and nord.country == "Germany" and nord.location == "Berlin, Germany"
    assert nord.linkedin_url == "https://www.linkedin.com/company/nordlicht"
    assert nord.description == "Nordlicht builds fleet software." and nord.data["theirstack_company_id"] == "nordlicht"
    assert [s.title for s in nord.signals] == ["Head of Finance", "Senior Accountant"]
    s = nord.signals[0]
    assert s.type == SignalType.JOB_POSTING and s.url == "https://boards.greenhouse.io/nordlicht/jobs/1"
    assert s.posted_at == date(2026, 9, 18) and s.location == "Berlin, Germany" and s.external_id == "1"
    assert s.description == "We are hiring a Head of Finance." and s.data["hybrid"] is True
    assert s.data["remote"] is False and s.source == "theirstack"
    assert [(c.full_name, c.title, c.source) for c in nord.contacts] == [
        ("Lena Vogel", "CFO", "theirstack"), ("Jonas Weber", "Finance Director", "theirstack")]
    assert nord.contacts[0].linkedin_url == "https://www.linkedin.com/in/lenavogel"
    assert alpen.location == "Vienna, Austria" and alpen.contacts == []


def test_theirstack_paging_stops_on_short_page_and_max_companies(make_ctx):
    src, ctx = theirstack(make_ctx, job_titles=["x"], limit=2, max_pages=5)
    ctx.http.add("POST", THEIRSTACK_URL, json=ts_page([ts_job(1, "A")], total=100))
    src.fetch()
    assert len(ctx.http.calls) == 1

    src, ctx = theirstack(make_ctx, job_titles=["x"], limit=2, max_pages=5, max_companies=1)
    ctx.http.add("POST", THEIRSTACK_URL, json=ts_page([ts_job(1, "A"), ts_job(2, "B", company="Other", domain="o.com")],
                                                      total=100))
    companies = src.fetch()
    assert [c.name for c in companies] == ["Nordlicht GmbH"] and len(ctx.http.calls) == 1


def test_theirstack_defaults_icp_hints_and_people_disabled(make_ctx):
    src, ctx = theirstack(make_ctx, icp={"employees": {"min": 10, "max": 200}}, people=None)
    ctx.http.add("POST", THEIRSTACK_URL, json=ts_page([ts_job(1, "A")], total=1))
    [c] = src.fetch()
    body = ctx.http.calls[0]["json"]
    assert body == {"posted_at_max_age_days": 30, "min_employee_count": 10, "max_employee_count": 200,
                    "page": 0, "limit": 25}
    assert c.contacts == []
    assert src.page_size == 25 and src.limit == 0


def test_theirstack_errors_and_credentials(make_ctx, caplog):
    src, ctx = theirstack(make_ctx)
    ctx.http.add("POST", THEIRSTACK_URL, status=402, json={"error": {"title": "Not enough credits"}})
    with pytest.raises(HttpError) as ei:
        src.fetch()
    assert ei.value.status == 402

    src, ctx = theirstack(make_ctx, limit=1, max_pages=3)
    ctx.http.add("POST", THEIRSTACK_URL, json=ts_page([ts_job(1, "A")], total=10), times=1)
    ctx.http.add("POST", THEIRSTACK_URL, status=500, json={"error": "boom"})
    with caplog.at_level(logging.WARNING):
        assert len(src.fetch()) == 1
    assert "page 1 failed" in caplog.text

    src, ctx = theirstack(make_ctx)
    ctx.http.add("POST", THEIRSTACK_URL, json={"metadata": {}, "data": [None, {"job_title": "No company"},
                                                                         {"company": "Bare Co", "job_title": "Ops"}]})
    [c] = src.fetch()
    assert c.name == "Bare Co" and c.signals[0].title == "Ops"

    src, ctx = theirstack(make_ctx, env={})
    with pytest.raises(MissingCredentialError, match="THEIRSTACK_API_KEY"):
        src.fetch()
    src, ctx = theirstack(make_ctx, dry_run=True)
    assert src.fetch() == [] and ctx.http.calls == []

    src, ctx = theirstack(make_ctx, filters="nope")
    with pytest.raises(ValueError, match="'filters' must be a mapping"):
        src.fetch()


# =====================================================================================
# registry
# =====================================================================================

@pytest.mark.parametrize("type_,cls,env_key", [
    ("apify", ApifySource, "APIFY_TOKEN"), ("apollo", ApolloSource, "APOLLO_API_KEY"),
    ("adzuna", AdzunaSource, "ADZUNA_APP_KEY"), ("theirstack", TheirStackSource, "THEIRSTACK_API_KEY"),
])
def test_registry_binds_api_sources(make_ctx, type_, cls, env_key):
    src = registry.create("source", {"type": type_}, make_ctx(env={}))  # no credential needed to build
    assert isinstance(src, cls) and src.name == type_ and src.env_key == env_key and src.offline is False
    assert src.label == type_
