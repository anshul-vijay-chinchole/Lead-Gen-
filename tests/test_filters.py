"""Tests for leadgen.filters: ICP checks, location aliases and fit checks."""
from __future__ import annotations

import logging
from datetime import timedelta

import pytest

from leadgen.filters import (apply_icp, canonical_place, check_icp, excluded_domain, fit_checks,
                             fit_report, is_placeless, match_location, place_ids)
from leadgen.models import Company, Signal
from tests.conftest import TODAY


def co(**kw) -> Company:
    kw.setdefault("name", "Acme")
    return Company(**kw)


def job(title: str = "Accountant", location: str = "", type_: str = "job_posting") -> Signal:
    return Signal(type=type_, title=title, location=location, posted_at=TODAY - timedelta(days=2))


# --- the simple checks --------------------------------------------------------------------

def test_defaults_let_everything_through(make_ctx):
    ctx = make_ctx()
    companies = [co(), co(name="Beta", domain="beta.io", employees=5, location="Nowhere")]
    kept, rejected = apply_icp(companies, ctx)
    assert kept == companies and rejected == []


def test_suppressed_domain_is_rejected_first(make_ctx):
    ctx = make_ctx(icp={"exclude_domains": ["acme.com"]})
    ctx.store.suppress("acme.com", "domain", "client asked")
    assert check_icp(co(domain="acme.com"), ctx) == "domain acme.com is on the suppression list"


def test_exclude_domains_exact_and_subdomain(make_ctx):
    ctx = make_ctx(icp={"exclude_domains": ["https://www.Acme.com/", "rival.io"]})
    assert check_icp(co(domain="acme.com"), ctx) == "excluded domain acme.com"
    assert check_icp(co(domain="eu.acme.com"), ctx) == "excluded domain eu.acme.com (under acme.com)"
    assert check_icp(co(domain="notacme.com"), ctx) is None
    assert check_icp(co(domain="rival.io"), ctx) is not None
    assert check_icp(co(domain=""), ctx) is None


def test_exclude_company_patterns(make_ctx):
    ctx = make_ctx(icp={"exclude_company_patterns": [r"recruit", r"^the\s+staffing"]})
    reason = check_icp(co(name="Global RECRUITMENT Partners"), ctx)
    assert reason == "company name 'Global RECRUITMENT Partners' matches excluded pattern 'recruit'"
    assert check_icp(co(name="The Staffing Co"), ctx) is not None
    assert check_icp(co(name="Acme Staffing"), ctx) is None


def test_invalid_pattern_is_ignored_with_warning(make_ctx, caplog):
    ctx = make_ctx()
    ctx.playbook.icp["exclude_company_patterns"] = ["(unclosed"]
    with caplog.at_level(logging.WARNING, logger="leadgen.test"):
        assert check_icp(co(), ctx) is None
    assert "invalid exclude_company_patterns" in caplog.text


def test_require_domain(make_ctx):
    ctx = make_ctx(icp={"require_domain": True})
    assert check_icp(co(), ctx) == "no website/domain (icp.require_domain is on)"
    assert check_icp(co(website="https://acme.com"), ctx) is None


@pytest.mark.parametrize("field,value,label", [
    ("name", "Acme Staffing", "name"),
    ("industry", "Staffing & Recruiting", "industry"),
    ("keywords", ["saas", "staffing"], "keywords"),
    ("description", "A staffing firm for nurses.", "description"),
])
def test_exclude_keywords_in_each_field(make_ctx, field, value, label):
    ctx = make_ctx(icp={"exclude_keywords": ["staffing"]})
    kw = {"name": "Acme", field: value}
    assert check_icp(Company(**kw), ctx) == f"excluded keyword 'staffing' in {label}"


def test_exclude_keywords_whole_word_only(make_ctx):
    ctx = make_ctx(icp={"exclude_keywords": ["hr"]})
    assert check_icp(co(description="Three offices across the region."), ctx) is None


def test_required_keywords_anywhere_including_signal_titles(make_ctx):
    ctx = make_ctx(icp={"keywords": ["accounting", "bookkeeping"]})
    assert check_icp(co(industry="Accounting"), ctx) is None
    assert check_icp(co(keywords=["tax", "bookkeeping"]), ctx) is None
    assert check_icp(co(signals=[job("Bookkeeping Assistant")]), ctx) is None
    assert check_icp(co(name="Acme Accounting"), ctx) is None
    reason = check_icp(co(description="We make shoes."), ctx)
    assert reason == ("no ICP keyword [accounting, bookkeeping] in name, industry, keywords, "
                      "description or signals")


def test_keywords_as_single_string_are_tolerated(make_ctx):
    ctx = make_ctx()
    ctx.playbook.icp["keywords"] = "saas"
    assert check_icp(co(keywords="b2b saas"), ctx) is None  # type: ignore[arg-type]


# --- location -------------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["United States", "USA", "U.S.", "US", "United States of America",
                                   "Austin, TX", "Remote - US", "New York, NY"])
def test_us_aliases(make_ctx, value):
    ctx = make_ctx(icp={"locations": ["United States"]})
    assert check_icp(co(location=value), ctx) is None, value


@pytest.mark.parametrize("crit", ["US", "USA", "U.S.", "United States"])
def test_us_criterion_spellings(make_ctx, crit):
    ctx = make_ctx(icp={"locations": [crit]})
    assert check_icp(co(country="USA"), ctx) is None


def test_us_never_matches_inside_a_word(make_ctx):
    ctx = make_ctx(icp={"locations": ["US"]})
    reason = check_icp(co(location="Houston"), ctx)
    assert reason == "location not in ICP [US] (got: Houston)"
    assert check_icp(co(location="Columbus"), ctx) is not None
    assert check_icp(co(location="Houston, TX"), ctx) is None  # the state says US


@pytest.mark.parametrize("value", ["United Kingdom", "UK", "U.K.", "GB", "Great Britain", "England",
                                   "Manchester, England", "Glasgow, Scotland", "Cardiff, Wales",
                                   "Belfast, Northern Ireland", "London, UK"])
def test_uk_aliases(make_ctx, value):
    ctx = make_ctx(icp={"locations": ["United Kingdom"]})
    assert check_icp(co(location=value), ctx) is None, value


def test_uk_parts_are_directional_and_lookalikes_dont_match(make_ctx):
    ctx = make_ctx(icp={"locations": ["England"]})
    assert check_icp(co(location="Leeds, England"), ctx) is None
    assert check_icp(co(location="Edinburgh, Scotland"), ctx) is not None
    assert check_icp(co(location="London, UK"), ctx) is not None  # can't tell which UK nation
    uk = make_ctx(icp={"locations": ["UK"]})
    assert check_icp(co(location="Sydney, New South Wales"), uk) is not None
    assert check_icp(co(location="Boston, New England"), uk) is not None
    assert check_icp(co(location="Dublin, Ireland"), uk) is not None


def test_state_names_and_codes(make_ctx):
    ctx = make_ctx(icp={"locations": ["Texas"]})
    assert check_icp(co(location="Dallas, TX"), ctx) is None
    assert check_icp(co(location="TX"), ctx) is None
    assert check_icp(co(location="Portland, OR"), ctx) is not None
    ctx2 = make_ctx(icp={"locations": ["TX", "ny"]})
    assert check_icp(co(location="Austin, Texas"), ctx2) is None
    assert check_icp(co(location="Brooklyn, New York"), ctx2) is None
    # lower-case two-letter words are never read as state codes
    ctx3 = make_ctx(icp={"locations": ["Indiana", "Oregon"]})
    assert check_icp(co(location="Remote in Europe or Asia"), ctx3) is not None


def test_country_field_iso_codes(make_ctx):
    ctx = make_ctx(icp={"locations": ["Germany", "Canada"]})
    assert check_icp(co(country="DE"), ctx) is None
    assert check_icp(co(country="CA"), ctx) is None
    assert check_icp(co(country="DEU"), ctx) is None
    # in a free-text location "CA" after a comma is California
    assert check_icp(co(location="San Jose, CA"), ctx) is not None


def test_regions_cover_member_countries(make_ctx):
    ctx = make_ctx(icp={"locations": ["Europe"]})
    assert check_icp(co(location="Berlin, Germany"), ctx) is None
    assert check_icp(co(location="London, UK"), ctx) is None
    assert check_icp(co(location="Remote (EU)"), ctx) is None
    assert check_icp(co(location="Austin, TX"), ctx) is not None


def test_plain_phrase_locations(make_ctx):
    ctx = make_ctx(icp={"locations": ["London", "Bay Area"]})
    assert check_icp(co(location="London, UK"), ctx) is None
    assert check_icp(co(location="San Francisco Bay Area"), ctx) is None
    assert check_icp(co(location="Londonderry"), ctx) is not None


def test_primary_signal_locations_count_for_inclusion(make_ctx):
    ctx = make_ctx(icp={"locations": ["United States"]})
    c = co(location="Berlin, Germany", signals=[job(location="Chicago, IL")])
    assert check_icp(c, ctx) is None
    # secondary signal locations are ignored
    c2 = co(location="Berlin, Germany", signals=[job(type_="funding", location="Chicago, IL")])
    assert check_icp(c2, ctx) is not None
    # with no company location, the signal location decides
    assert check_icp(co(signals=[job(location="Denver, CO")]), ctx) is None


def test_exclude_locations(make_ctx):
    ctx = make_ctx(icp={"exclude_locations": ["India"]})
    assert check_icp(co(country="India"), ctx) == "excluded location 'India' (India)"
    assert check_icp(co(country="IN"), ctx) is not None
    # a single posting abroad does not disqualify a company based elsewhere
    assert check_icp(co(location="Austin, TX", signals=[job(location="Bangalore, India")]), ctx) is None
    # unknown home + every signal in an excluded place => excluded
    reason = check_icp(co(signals=[job(location="Pune, India"), job("Clerk", location="Delhi, India")]), ctx)
    assert reason == "excluded location 'India' (signals in Pune, India, Delhi, India)"
    assert check_icp(co(signals=[job(location="Pune, India"), job("Clerk", location="Austin, TX")]), ctx) is None


def test_exclusion_wins_over_inclusion(make_ctx):
    ctx = make_ctx(icp={"locations": ["United Kingdom"], "exclude_locations": ["Scotland"]})
    assert check_icp(co(location="Glasgow, Scotland"), ctx) == "excluded location 'Scotland' (Glasgow, Scotland)"
    assert check_icp(co(location="Leeds, England"), ctx) is None


def test_unknown_location_follows_unknown_passes(make_ctx):
    ctx = make_ctx(icp={"locations": ["US"]})
    assert check_icp(co(), ctx) is None
    assert check_icp(co(location="Remote"), ctx) is None  # not a place => unknown
    strict = make_ctx(icp={"locations": ["US"], "unknown_passes": False})
    assert check_icp(co(), strict) == "location unknown (icp.unknown_passes is off)"
    assert check_icp(co(location="Multiple Locations"), strict) is not None


def test_location_helpers():
    assert canonical_place("usa") == "united states" and canonical_place("GB") == "united kingdom"
    assert canonical_place("CA") == "california" and canonical_place("Canada") == "canada"
    assert canonical_place("London") is None and canonical_place("") is None
    assert "united states" in place_ids("Seattle, WA")
    assert place_ids("") == frozenset()
    assert place_ids("Houston") == frozenset()
    assert "germany" in place_ids("DE", True) and "delaware" in place_ids("DE")
    assert match_location(["US", "UK"], "Leeds, England") == "UK"
    assert match_location(["US"], "") is None
    assert is_placeless("Remote / Hybrid") and is_placeless("N/A") and is_placeless("")
    assert not is_placeless("Remote - London")


# --- size ------------------------------------------------------------------------------------

def test_employee_range(make_ctx):
    ctx = make_ctx(icp={"employees": {"min": 10, "max": 500}})
    assert check_icp(co(employees=10), ctx) is None
    assert check_icp(co(employees=500), ctx) is None
    assert check_icp(co(employees=9), ctx) == "too small (9 employees, min 10)"
    assert check_icp(co(employees=501), ctx) == "too large (501 employees, max 500)"
    assert check_icp(co(employees="51-200"), ctx) is None
    only_min = make_ctx(icp={"employees": {"min": 50, "max": None}})
    assert check_icp(co(employees=100000), only_min) is None


def test_unknown_size(make_ctx):
    assert check_icp(co(), make_ctx(icp={"employees": {"min": 10}})) is None
    strict = make_ctx(icp={"employees": {"min": 10}, "unknown_passes": False})
    assert check_icp(co(), strict) == "size unknown (icp.unknown_passes is off)"


# --- industry ------------------------------------------------------------------------------

def test_industry_positive_match_sources(make_ctx):
    ctx = make_ctx(icp={"industries": ["software", "fintech"]})
    assert check_icp(co(industry="Computer Software"), ctx) is None
    assert check_icp(co(keywords=["payments", "fintech"]), ctx) is None
    assert check_icp(co(description="We build software for dentists."), ctx) is None
    reason = check_icp(co(industry="Retail", description="Shoes."), ctx)
    assert reason == "industry not in ICP [software, fintech] (got: Retail)"


def test_industry_exclusion_ignores_description(make_ctx):
    ctx = make_ctx(icp={"exclude_industries": ["staffing"]})
    assert check_icp(co(industry="Staffing and Recruiting"), ctx) == \
        "excluded industry 'staffing' (Staffing and Recruiting)"
    assert check_icp(co(keywords=["staffing"]), ctx) is not None
    assert check_icp(co(industry="Software", description="We replace staffing agencies."), ctx) is None


def test_unknown_industry(make_ctx):
    ctx = make_ctx(icp={"industries": ["software"]})
    assert check_icp(co(), ctx) is None
    strict = make_ctx(icp={"industries": ["software"], "unknown_passes": False})
    assert check_icp(co(), strict) == "industry unknown (icp.unknown_passes is off)"


def test_check_order_first_failure_wins(make_ctx):
    ctx = make_ctx(icp={"locations": ["UK"], "employees": {"min": 100}, "industries": ["software"]})
    c = co(location="Paris, France", employees=5, industry="Retail")
    assert check_icp(c, ctx).startswith("location not in ICP")
    c.location = "London, UK"
    assert check_icp(c, ctx).startswith("too small")
    c.employees = 500
    assert check_icp(c, ctx).startswith("industry not in ICP")


# --- fit checks --------------------------------------------------------------------------------

def test_fit_checks_values(make_ctx):
    ctx = make_ctx(icp={"locations": ["US"], "employees": {"min": 10, "max": 100},
                        "industries": ["software"]})
    assert fit_checks(co(location="Austin, TX", employees=50, industry="Software"), ctx) == \
        {"location": True, "size": True, "industry": True}
    assert fit_checks(co(location="Paris, France", employees=5000, industry="Retail"), ctx) == \
        {"location": False, "size": False, "industry": False}
    assert fit_checks(co(), ctx) == {"location": None, "size": None, "industry": None}


def test_fit_checks_unconfigured_is_true(make_ctx):
    ctx = make_ctx()
    assert fit_checks(co(), ctx) == {"location": True, "size": True, "industry": True}
    report = fit_report(co(), ctx)
    assert all(not r.configured for r in report.values())


def test_apply_icp_returns_reasons(make_ctx):
    ctx = make_ctx(icp={"exclude_domains": ["bad.com"]})
    good, bad = co(domain="good.com"), co(name="Bad", domain="bad.com")
    kept, rejected = apply_icp([good, bad], ctx)
    assert kept == [good] and rejected == [(bad, "excluded domain bad.com")]


def test_codes_that_look_like_filler_words_are_places():
    # "in", "or" are filler words in free text, but as codes they name places
    assert not is_placeless("IN", country_field=True)
    assert not is_placeless("OR")          # Oregon, as a whole-value state code
    assert is_placeless("in")              # lower case free text: not a place


# --- regressions (verifier findings) -----------------------------------------------------

@pytest.mark.parametrize("location,country,iso", [
    ("Berlin, DE", "DE", "germany"),
    ("Bangalore, IN", "India", "india"),
    ("Tel Aviv, IL", "IL", "israel"),
    ("Toronto, CA", "Canada", "canada"),
])
def test_state_like_iso_code_follows_the_company_country(make_ctx, location, country, iso):
    company = co(domain="acme.example", location=location, country=country)
    inc = make_ctx(icp={"locations": ["United States"]})
    assert check_icp(company, inc) is not None  # not accepted as a US company
    exc = make_ctx(icp={"exclude_locations": ["United States"]})
    assert check_icp(company, exc) is None  # not rejected as a US company
    assert check_icp(company, make_ctx(icp={"locations": [iso]})) is None


@pytest.mark.parametrize("text,expected,not_expected", [
    ("Toronto, ON, CA", "canada", "united states"),
    ("Toronto, Ontario, CA", "canada", "united states"),
    ("Bangalore, Karnataka, IN", "india", "united states"),
    ("Tel Aviv, IL, Israel", "israel", "united states"),
    ("Berlin, DE, Europe", "germany", "united states"),
    ("Chicago, IL", "illinois", "israel"),
    ("San Francisco, CA", "california", "canada"),
    ("Wilmington, DE, US", "delaware", "germany"),
    ("Chicago, Illinois, IL", "illinois", "israel"),
    ("Springfield, Sangamon County, IL", "illinois", "israel"),
])
def test_state_like_iso_code_read_from_the_string(text, expected, not_expected):
    ids = place_ids(text)
    assert expected in ids and not_expected not in ids


def test_us_company_country_keeps_state_reading(make_ctx):
    ctx = make_ctx(icp={"locations": ["United States"]})
    assert check_icp(co(location="Wilmington, DE", country="US"), ctx) is None
    assert check_icp(co(location="Chicago, IL"), ctx) is None
    assert place_ids("CA", False, "canada") == place_ids("Canada")


def test_excluded_domain_helper(make_ctx):
    ctx = make_ctx(icp={"exclude_domains": ["bigclient.com", "https://www.rival.io/"]})
    assert excluded_domain("jane@bigclient.com", ctx) == "bigclient.com"
    assert excluded_domain("Jane@EU.BigClient.com", ctx) == "bigclient.com"
    assert excluded_domain("https://rival.io/about", ctx) == "rival.io"
    assert excluded_domain("jane@notbigclient.com", ctx) is None
    assert excluded_domain("", ctx) is None and excluded_domain(None, ctx) is None
    assert excluded_domain("jane@bigclient.com", make_ctx()) is None
    ctx.playbook.icp["exclude_domains"].append("late.example")  # edited in place (e.g. a server)
    assert excluded_domain("x@late.example", ctx) == "late.example"
