"""Tests for the public ATS job-board sources (Greenhouse, Lever, Ashby)."""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

import pytest

from leadgen import registry
from leadgen.http import HttpError
from leadgen.models import SignalType
from leadgen.sources.ats import AshbySource, GreenhouseSource, LeverSource

GH = "https://boards-api.greenhouse.io/v1/boards"

GREENHOUSE_ACME = {
    "jobs": [
        {
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/4001",
            "data_compliance": [{"type": "gdpr", "requires_consent": False, "retention_period": None}],
            "internal_job_id": 3001, "location": {"name": "Remote - Europe"}, "metadata": None, "id": 4001,
            "updated_at": "2026-09-20T12:00:00-04:00", "requisition_id": "R-101", "title": "Senior Accountant",
            "first_published": "2026-09-15T09:00:00-04:00", "company_name": "Acme",
            "content": "&lt;div class=&quot;content-intro&quot;&gt;&lt;p&gt;Join &lt;strong&gt;Acme&lt;/strong&gt; "
                       "&amp;amp; grow.&lt;/p&gt;&lt;/div&gt;&lt;ul&gt;&lt;li&gt;IFRS&lt;/li&gt;&lt;li&gt;SAP&lt;/li&gt;&lt;/ul&gt;",
            "departments": [{"id": 11, "name": "Finance", "child_ids": [], "parent_id": None}],
            "offices": [{"id": 21, "name": "Remote", "location": "Remote", "child_ids": [], "parent_id": None}],
        },
        {
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/4002", "internal_job_id": 3002,
            "location": {"name": "Berlin, Germany"}, "id": 4002, "updated_at": "2026-09-22T08:00:00Z",
            "title": "Payroll Specialist", "first_published": None, "content": "", "departments": [],
        },
        {"id": 4003, "title": "", "location": {"name": "Berlin"}},  # no title -> ignored
    ],
    "meta": {"total": 3},
}

LEVER_BETA = [
    {
        "additional": "<div>Benefits</div>", "additionalPlain": "Benefits",
        "categories": {"commitment": "Full-time", "department": "Finance", "location": "London",
                       "team": "Accounting", "allLocations": ["London"]},
        "createdAt": int(datetime(2026, 9, 10, 9, 0, tzinfo=timezone.utc).timestamp() * 1000),
        "descriptionPlain": "About the role: own month-end close.", "description": "<div>About the role</div>",
        "id": "a1b2c3d4-0000-4000-8000-000000000001", "lists": [], "text": "Financial Controller",
        "country": "GB", "workplaceType": "hybrid",
        "hostedUrl": "https://jobs.lever.co/beta/a1b2c3d4-0000-4000-8000-000000000001",
        "applyUrl": "https://jobs.lever.co/beta/a1b2c3d4-0000-4000-8000-000000000001/apply",
    },
    {
        "categories": {"commitment": "Part-time", "team": "Ops", "allLocations": ["Leeds", "York"]},
        "createdAt": int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp() * 1000),
        "id": "a1b2c3d4-0000-4000-8000-000000000002", "text": "Office Coordinator",
        "hostedUrl": "https://jobs.lever.co/beta/a1b2c3d4-0000-4000-8000-000000000002",
    },
]

ASHBY_GAMMA = {
    "apiVersion": "1",
    "jobs": [
        {
            "id": "5f7c7c7c-1111-4222-8333-444455556666", "title": "Finance Manager", "department": "Finance",
            "team": "FP&A", "employmentType": "FullTime", "location": "New York", "secondaryLocations": [],
            "publishedAt": "2026-09-12T15:30:00.000+00:00", "isListed": True, "isRemote": False,
            "address": {"postalAddress": {"addressLocality": "New York", "addressRegion": "NY",
                                          "addressCountry": "United States"}},
            "jobUrl": "https://jobs.ashbyhq.com/gamma/5f7c7c7c-1111-4222-8333-444455556666",
            "applyUrl": "https://jobs.ashbyhq.com/gamma/5f7c7c7c-1111-4222-8333-444455556666/application",
            "descriptionHtml": "<p>Lead planning</p>", "descriptionPlain": "Lead planning",
        },
        {"id": "hidden-1", "title": "Secret Role", "isListed": False, "jobUrl": "https://jobs.ashbyhq.com/gamma/hidden-1"},
        {
            "id": "7a7a7a7a-1111-4222-8333-444455556666", "title": "Staff Accountant", "location": None,
            "address": {"postalAddress": {"addressLocality": "Austin", "addressRegion": "TX"}},
            "publishedAt": "2026-09-20T10:00:00.000+00:00", "isListed": True,
            "jobUrl": "https://jobs.ashbyhq.com/gamma/7a7a7a7a", "descriptionHtml": "<p>Close the <em>books</em></p>",
        },
    ],
}


def make(cls, make_ctx, dry_run=False, **config):
    ctx = make_ctx(dry_run=dry_run)
    config.setdefault("type", cls.name)
    return cls(config, ctx), ctx


def test_greenhouse_watchlist(make_ctx, caplog):
    src, ctx = make(GreenhouseSource, make_ctx, companies=[
        {"name": "Acme", "board": "acme", "domain": "acme.com", "location": "Berlin, Germany",
         "industry": "Software", "employees": "250"},
        {"name": "Ghost Co", "board": "ghostco", "domain": "ghost.co"},
        {"name": "Quiet Inc", "board": "quiet", "domain": "quiet.io"},
    ])
    ctx.http.add("GET", f"{GH}/acme/jobs", json=GREENHOUSE_ACME)
    ctx.http.add("GET", f"{GH}/ghostco/jobs", status=404, json={"status": 404, "error": "Job not found"})
    ctx.http.add("GET", f"{GH}/quiet/jobs", json={"jobs": [], "meta": {"total": 0}})
    with caplog.at_level(logging.WARNING):
        companies = src.fetch()
    assert "ghostco" in caplog.text and "404" in caplog.text
    assert [c["url"] for c in ctx.http.calls] == [f"{GH}/acme/jobs", f"{GH}/ghostco/jobs", f"{GH}/quiet/jobs"]
    assert ctx.http.calls[0]["params"] == {"content": "true"}

    [acme] = companies  # 404 skipped, board without jobs omitted
    assert acme.name == "Acme" and acme.domain == "acme.com" and acme.location == "Berlin, Germany"
    assert acme.industry == "Software" and acme.employees == 250 and acme.sources == ["greenhouse"]
    assert acme.data["ats"] == "greenhouse" and acme.data["ats_board"] == "acme" and acme.data["open_jobs"] == 2
    # freshest first
    assert [s.title for s in acme.signals] == ["Payroll Specialist", "Senior Accountant"]
    payroll, senior = acme.signals
    assert payroll.posted_at == date(2026, 9, 22)  # falls back to updated_at
    assert senior.type == SignalType.JOB_POSTING and senior.posted_at == date(2026, 9, 15)
    assert senior.url == "https://boards.greenhouse.io/acme/jobs/4001" and senior.external_id == "4001"
    assert senior.location == "Remote - Europe" and senior.source == "greenhouse"
    assert senior.description == "Join Acme & grow. IFRS SAP"  # escaped HTML decoded + stripped
    assert senior.data["department"] == "Finance"


def test_greenhouse_location_falls_back_to_job_locations(make_ctx):
    src, ctx = make(GreenhouseSource, make_ctx, companies=["acme"], include_content=False)
    ctx.http.add("GET", f"{GH}/acme/jobs", json=GREENHOUSE_ACME)
    [c] = src.fetch()
    assert ctx.http.calls[0]["params"] == {}
    assert c.name == "acme" and c.domain == ""
    assert c.location == "Berlin, Germany; Remote - Europe"
    assert c.data["job_locations"] == ["Berlin, Germany", "Remote - Europe"]


def test_lever_postings_and_eu_region(make_ctx):
    src, ctx = make(LeverSource, make_ctx, companies=[
        {"name": "Beta Ltd", "board": "beta", "website": "https://www.beta.co.uk"},
        {"name": "Euro GmbH", "board": "euro", "region": "eu"},
    ])
    ctx.http.add("GET", "https://api.lever.co/v0/postings/beta", json=LEVER_BETA)
    ctx.http.add("GET", "https://api.eu.lever.co/v0/postings/euro", json=[])
    [beta] = src.fetch()
    assert [c["url"] for c in ctx.http.calls] == ["https://api.lever.co/v0/postings/beta",
                                                  "https://api.eu.lever.co/v0/postings/euro"]
    assert ctx.http.calls[0]["params"] == {"mode": "json"}
    assert beta.domain == "beta.co.uk" and beta.website == "https://www.beta.co.uk"
    fc, oc = beta.signals
    assert fc.title == "Financial Controller" and fc.posted_at == date(2026, 9, 10)
    assert fc.url.startswith("https://jobs.lever.co/beta/") and fc.location == "London"
    assert fc.description == "About the role: own month-end close."
    assert fc.data == {"department": "Finance", "team": "Accounting", "commitment": "Full-time", "ats": "lever"}
    assert oc.location == "Leeds, York" and oc.posted_at == date(2026, 9, 1)


def test_lever_region_from_config_and_error_envelope(make_ctx, caplog):
    src, ctx = make(LeverSource, make_ctx, region="EU", companies=[{"name": "A", "board": "a"},
                                                                    {"name": "B", "board": "b"}])
    ctx.http.add("GET", "https://api.eu.lever.co/v0/postings/a", json={"ok": False, "error": "Document not found"})
    ctx.http.add("GET", "https://api.eu.lever.co/v0/postings/b", json=LEVER_BETA[:1])
    with caplog.at_level(logging.WARNING):
        [b] = src.fetch()
    assert b.name == "B" and "unexpected payload" in caplog.text


def test_ashby_board_skips_unlisted(make_ctx):
    src, ctx = make(AshbySource, make_ctx, companies=[{"name": "Gamma", "board": "gamma", "domain": "gamma.dev"}])
    ctx.http.add("GET", "https://api.ashbyhq.com/posting-api/job-board/gamma", json=ASHBY_GAMMA)
    [g] = src.fetch()
    assert ctx.http.calls[0]["params"] == {"includeCompensation": "false"}
    assert [s.title for s in g.signals] == ["Staff Accountant", "Finance Manager"]
    staff, fm = g.signals
    assert staff.location == "Austin, TX" and staff.description == "Close the books"
    assert fm.posted_at == date(2026, 9, 12) and fm.location == "New York"
    assert fm.data == {"department": "Finance", "team": "FP&A", "commitment": "FullTime", "ats": "ashby"}
    assert fm.external_id == "5f7c7c7c-1111-4222-8333-444455556666" and g.sources == ["ashby"]
    assert all(s.title != "Secret Role" for s in g.signals)


def test_all_boards_failing_raises(make_ctx):
    src, ctx = make(AshbySource, make_ctx, companies=[{"name": "A", "board": "a"}, {"name": "B", "board": "b"}])
    ctx.http.add("GET", "https://api.ashbyhq.com/posting-api/job-board/", status=404, json={"error": "nope"})
    with pytest.raises(HttpError) as ei:
        src.fetch()
    assert ei.value.status == 404


def test_server_error_on_one_board_continues(make_ctx, caplog):
    src, ctx = make(GreenhouseSource, make_ctx, companies=[{"name": "Down", "board": "down"},
                                                           {"name": "Acme", "board": "acme"}])
    ctx.http.add("GET", f"{GH}/down/jobs", status=503, json={"error": "unavailable"})
    ctx.http.add("GET", f"{GH}/acme/jobs", json=GREENHOUSE_ACME)
    with caplog.at_level(logging.WARNING):
        [c] = src.fetch()
    assert c.name == "Acme" and "failed" in caplog.text


def test_non_json_board_response_is_skipped(make_ctx, caplog):
    src, ctx = make(GreenhouseSource, make_ctx, companies=[{"name": "Html", "board": "html"},
                                                           {"name": "Acme", "board": "acme"}])
    ctx.http.add("GET", f"{GH}/html/jobs", text="<html>Down for maintenance</html>")
    ctx.http.add("GET", f"{GH}/acme/jobs", json=GREENHOUSE_ACME)
    with caplog.at_level(logging.WARNING):
        [c] = src.fetch()
    assert c.name == "Acme" and "did not return JSON" in caplog.text
    src, ctx = make(GreenhouseSource, make_ctx, companies=[{"name": "Html", "board": "html"}])
    ctx.http.add("GET", f"{GH}/html/jobs", text="<html>Down</html>")
    with pytest.raises(ValueError, match="did not return JSON"):
        src.fetch()


def test_limit_and_max_jobs_per_company(make_ctx):
    src, ctx = make(GreenhouseSource, make_ctx, limit=1, max_jobs_per_company=1,
                    companies=[{"name": "Acme", "board": "acme"}, {"name": "Other", "board": "other"}])
    ctx.http.add("GET", f"{GH}/acme/jobs", json=GREENHOUSE_ACME)
    [c] = src.fetch()
    assert len(ctx.http.calls) == 1  # limit reached -> the next board is not fetched
    assert [s.title for s in c.signals] == ["Payroll Specialist"] and c.data["open_jobs"] == 1


def test_watchlist_validation(make_ctx, caplog):
    src, ctx = make(GreenhouseSource, make_ctx)
    with pytest.raises(ValueError, match="'companies'"):
        src.fetch()
    src, ctx = make(GreenhouseSource, make_ctx, companies="acme-only")
    ctx.http.add("GET", f"{GH}/acme-only/jobs", json=GREENHOUSE_ACME)
    assert [c.name for c in src.fetch()] == ["acme-only"]
    src, ctx = make(GreenhouseSource, make_ctx, companies=[{"name": "No Board"}, {"board": "acme", "name": "Acme"}])
    ctx.http.add("GET", f"{GH}/acme/jobs", json=GREENHOUSE_ACME)
    with caplog.at_level(logging.WARNING):
        assert [c.name for c in src.fetch()] == ["Acme"]
    assert "has no 'board'" in caplog.text
    src, ctx = make(GreenhouseSource, make_ctx, board="acme", name="Acme Single", domain="acme.com")
    ctx.http.add("GET", f"{GH}/acme/jobs", json=GREENHOUSE_ACME)
    [c] = src.fetch()
    assert c.name == "Acme Single" and c.domain == "acme.com"
    src, ctx = make(GreenhouseSource, make_ctx, companies=42)
    with pytest.raises(ValueError, match="must be a list"):
        src.fetch()


def test_board_names_are_url_escaped(make_ctx):
    src, ctx = make(AshbySource, make_ctx, companies=[{"name": "X", "board": "x y/z"}])
    ctx.http.add("GET", "https://api.ashbyhq.com/posting-api/job-board/x%20y%2Fz", json={"jobs": []})
    assert src.fetch() == []


def test_malformed_payloads_are_tolerated(make_ctx):
    src, ctx = make(GreenhouseSource, make_ctx, companies=[{"name": "A", "board": "a"}])
    ctx.http.add("GET", f"{GH}/a/jobs", json={"jobs": [None, "junk", {"title": "Ops Lead", "location": "Oslo"}]})
    [c] = src.fetch()
    assert [s.title for s in c.signals] == ["Ops Lead"] and c.signals[0].location == "Oslo"
    src, ctx = make(AshbySource, make_ctx, companies=[{"name": "A", "board": "a"}])
    ctx.http.add("GET", "https://api.ashbyhq.com/posting-api/job-board/a", json=["unexpected"])
    assert src.fetch() == []


def test_dry_run_makes_no_calls(make_ctx):
    for cls in (GreenhouseSource, LeverSource, AshbySource):
        src, ctx = make(cls, make_ctx, dry_run=True, companies=[{"name": "A", "board": "a"}])
        assert src.fetch() == [] and ctx.http.calls == []


@pytest.mark.parametrize("type_,cls", [("greenhouse", GreenhouseSource), ("lever", LeverSource),
                                       ("ashby", AshbySource)])
def test_registry_binds_ats_sources(make_ctx, type_, cls):
    src = registry.create("source", {"type": type_, "label": f"{type_}-watch"}, make_ctx())
    assert isinstance(src, cls) and src.name == type_ and src.offline is False and src.env_key == ""
    assert src.label == f"{type_}-watch"
