"""Unit tests for leadgen.sources.mapping (the shared record -> Company machinery)."""
from __future__ import annotations

import logging
from datetime import date

import pytest

from leadgen.models import Company, EmailStatus, SignalType
from leadgen.sources.mapping import (
    CompanyCollector,
    RecordMapper,
    as_text,
    canonical_field,
    clean_domain,
    clean_email,
    clean_url,
    detect_mapping,
    format_money,
    funding_signal,
    is_blank,
    merge_mappings,
    normalize_email_status,
    normalize_header,
    parse_amount,
    parse_when,
    pretty_stage,
    records_to_companies,
    render_template,
    resolve_columns,
    split_keywords,
    strip_html,
)

TODAY = date(2026, 9, 24)


# --- field names ------------------------------------------------------------------------

def test_canonical_field_aliases_and_errors():
    assert canonical_field("name") == "name"
    assert canonical_field("Company Name") == "name"
    assert canonical_field("company_linkedin_url") == "linkedin_url"
    assert canonical_field("organization_domain") == "domain"
    assert canonical_field("person_title") == "title"
    assert canonical_field("contact_email") == "email"
    assert canonical_field("person_linkedin_url") == "person_linkedin_url"
    assert canonical_field("contact_linkedin_url") == "person_linkedin_url"
    assert canonical_field("person_city") == "person_city"
    assert canonical_field("employee_count") == "employees"
    assert canonical_field("data.Rating") == "data.Rating"
    assert canonical_field("signal_data.salary") == "signal_data.salary"
    assert canonical_field("linkedin_url", person_context=True) == "person_linkedin_url"
    assert canonical_field("role", person_context=True) == "title"
    with pytest.raises(ValueError, match="unknown mapping field 'favourite_colour'"):
        canonical_field("favourite_colour")


def test_merge_mappings_override_and_disable():
    m = merge_mappings({"name": "a", "industry": ["x", "y"]}, {"company_name": "b", "industry": None})
    assert m == {"name": ["b"], "industry": []}
    with pytest.raises(ValueError):
        merge_mappings(["not", "a", "dict"])  # type: ignore[arg-type]


# --- value helpers --------------------------------------------------------------------------

def test_blank_and_text():
    for v in (None, "", "  ", "N/A", "null", "None", "-", [], {}, float("nan")):
        assert is_blank(v), v
    for v in (0, False, "0", "NA", ["x"]):
        assert not is_blank(v), v
    assert as_text(12.0) == "12" and as_text(4.5) == "4.5" and as_text(True) == ""
    assert as_text({"display_name": "Leeds"}) == "Leeds" and as_text({"x": 1}) == ""
    assert as_text(["a", "b", "A", None]) == "a, b"


def test_strip_html_variants():
    assert strip_html("<p>Hello <b>world</b>!</p><p>Bye</p>") == "Hello world! Bye"
    assert strip_html("&lt;p&gt;Escaped &amp;amp; ok&lt;/p&gt;") == "Escaped & ok"
    assert strip_html("Salary < 50k & > 40k") == "Salary < 50k & > 40k"
    assert strip_html("<script>var x=1;</script><style>p{}</style>Text<!-- c -->") == "Text"
    assert strip_html("a&nbsp;b<br/>c") == "a b c"
    assert strip_html(None) == ""


def test_domain_email_url_cleaning():
    assert clean_domain("https://www.Acme.com/about") == "acme.com"
    assert clean_domain("jane@acme.io") == "acme.io"
    assert clean_domain("N/A") == "" and clean_domain("localhost") == ""
    assert clean_domain("https://www.linkedin.com/company/acme") == ""
    assert clean_domain("https://m.facebook.com/acme") == ""
    assert clean_email("Jane@Acme.com") == "jane@acme.com"
    assert clean_email("mailto:jane@acme.com") == "jane@acme.com"
    assert clean_email("email_not_unlocked@domain.com") == ""
    assert clean_email("not an email") == ""
    assert clean_url("https://x.com/a?b=1&amp;c=2") == "https://x.com/a?b=1&c=2"
    assert clean_url("see website") == "" and clean_url("n/a") == ""


@pytest.mark.parametrize("raw,expected", [
    ("Verified", EmailStatus.VALID), ("valid", EmailStatus.VALID), ("Deliverable", EmailStatus.VALID),
    ("catch-all", EmailStatus.RISKY), ("catch_all", EmailStatus.RISKY), ("Accept All", EmailStatus.RISKY),
    ("risky", EmailStatus.RISKY), ("invalid", EmailStatus.INVALID), ("Bounced", EmailStatus.INVALID),
    ("undeliverable", EmailStatus.INVALID), ("guessed", EmailStatus.UNKNOWN),
    ("Extrapolated", EmailStatus.UNKNOWN), ("unavailable", EmailStatus.UNKNOWN), ("unknown", EmailStatus.UNKNOWN),
    ("Unverified", EmailStatus.UNKNOWN), ("", EmailStatus.UNKNOWN), (None, EmailStatus.UNKNOWN),
    ("weird-status", EmailStatus.UNKNOWN),
])
def test_email_status_normalisation(raw, expected):
    assert normalize_email_status(raw) == expected


def test_split_keywords():
    assert split_keywords("a, b; c | a") == ["a", "b", "c"]
    assert split_keywords(["x", None, "y"]) == ["x", "y"]
    assert split_keywords(None) == []


# --- dates / money / funding --------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("3 days ago", date(2026, 9, 21)), ("30+ days ago", date(2026, 8, 25)), ("Posted 2 weeks ago", date(2026, 9, 10)),
    ("an hour ago", TODAY), ("Just posted", TODAY), ("Today", TODAY), ("yesterday", date(2026, 9, 23)),
    ("1 month ago", date(2026, 8, 25)), ("2d ago", date(2026, 9, 22)), ("Posted on 2026-09-01", date(2026, 9, 1)),
    ("2026-09-20T10:00:00Z", date(2026, 9, 20)), (date(2026, 1, 2), date(2026, 1, 2)), ("garbage", None),
    (None, None), (True, None),
])
def test_parse_when(raw, expected):
    assert parse_when(raw, TODAY) == expected


def test_money_and_stage():
    assert parse_amount("$12M") == (12_000_000.0, "$")
    assert parse_amount("12,500,000") == (12_500_000.0, "")
    assert parse_amount("EUR 5.5m") == (5_500_000.0, "EUR")
    assert parse_amount("undisclosed") == (None, "")
    assert format_money(12_000_000) == "$12M" and format_money("1500000") == "$1.5M"
    assert format_money(750_000) == "$750K" and format_money(2.5e9) == "$2.5B"
    assert format_money("€5.5M") == "€5.5M" and format_money(3_000_000, "EUR") == "3M EUR"
    assert format_money(950) == "$950"
    assert pretty_stage("series_a") == "Series A" and pretty_stage("seed") == "Seed"
    assert pretty_stage("pre_seed") == "Pre-Seed" and pretty_stage("SERIES_B") == "Series B"
    assert pretty_stage("Venture (Round not Specified)") == "Venture (Round not Specified)"


def test_funding_signal_variants():
    s = funding_signal("series_a", "12000000", "2026-08-01", label="x", total=30_000_000, url="https://n.ex/a")
    assert s.type == SignalType.FUNDING and s.title == "Raised Series A ($12M)" and s.source == "x"
    assert s.posted_at == date(2026, 8, 1) and s.url == "https://n.ex/a"
    assert s.data == {"stage": "Series A", "amount": 12_000_000.0, "total": 30_000_000.0}
    assert s.description == "Total funding to date: $30M"
    assert funding_signal("Seed", None, None).title == "Raised Seed"
    assert funding_signal(None, "$3M", None).title == "Raised $3M"
    assert funding_signal(None, None, "2026-09-01").title == "Raised a funding round"
    assert funding_signal("ipo", None, "2026-01-01").title == "Went public (IPO)"
    assert funding_signal("Acquired", None, None).title == "Acquired"
    assert funding_signal(None, None, None) is None
    assert funding_signal("", "undisclosed", "") is None


# --- templates ------------------------------------------------------------------------------------

def test_render_template():
    rec = {"totalScore": 4.56, "reviewsCount": 0, "loc": {"city": "Leeds"}, "tags": ["a", "b"], "Job Title": "CFO"}
    assert render_template("Rated {totalScore:.1f} ({reviewsCount} reviews)", rec) == "Rated 4.6 (0 reviews)"
    assert render_template("In {loc.city} / {tags} / {tags[0]}", rec) == "In Leeds / a, b / a"
    assert render_template("{Job Title} wanted", rec) == "CFO wanted"
    assert render_template("{missing} and {also_missing}", rec) == ""
    assert render_template("Hi {missing:.1f}", {"missing": "abc"}) == "Hi abc"  # bad spec -> plain text
    assert render_template("Static title", {}) == "Static title"
    assert render_template("{name} in {city}", {}, {"name": "Acme", "city": ""}) == "Acme in"
    assert render_template("Booth ({b})", {}, require_value=False) == "Booth"


# --- CompanyCollector --------------------------------------------------------------------------------

def test_collector_groups_and_limits():
    col = CompanyCollector(limit=2)
    assert col.add(Company(name="Acme Inc", domain="acme.com"))
    assert col.add(Company(name="Acme", domain=""))            # same normalized name -> merged
    assert col.add(Company(name="ACME Holdings", domain="acme.com"))  # same domain -> merged
    assert col.add(Company(name="Apex", domain="apex.com"))
    assert not col.add(Company(name="Third", domain="third.com"))  # limit reached
    assert col.add(Company(name="Apex", domain="apex.com"))    # known company still merges
    assert col.add(Company(name="Apex", domain="apex.io")) is False  # same name, other domain = new company
    assert [c.name for c in col.companies] == ["Acme Inc", "Apex"] and col.dropped == 2 and col.full
    assert col.add(None) is False
    assert CompanyCollector(limit="bad").limit == 0  # type: ignore[arg-type]


# --- RecordMapper / records_to_companies ------------------------------------------------------------

def test_record_mapper_full_record():
    mapping = {
        "name": "org.name", "website": "org.site", "linkedin_url": "org.li", "employees": "org.size",
        "industry": "org.industry", "keywords": "org.tags", "city": "hq.city", "state": "hq.state",
        "country": "hq.country", "description": "org.about",
        "first_name": "person.first", "last_name": "person.last", "title": "person.title",
        "email": "person.email", "email_status": "person.status", "person_linkedin_url": "person.li",
        "phone": ["person.mobile", "person.phone"], "seniority": "person.level", "department": "person.dept",
        "person_city": "person.city", "person_data.source_id": "person.id",
        "signal_title": "job.title", "signal_date": "job.date", "signal_url": "job.url", "signal_id": "job.id",
        "signal_location": "job.where", "signal_description": "job.text", "signal_data.salary": "job.pay",
        "funding_stage": "fund.stage", "funding_amount": "fund.amount", "funding_date": "fund.date",
        "data.rating": "org.rating",
    }
    rec = {
        "org": {"name": " Acme &amp; Sons ", "site": "https://www.acme.com/", "li": "https://linkedin.com/company/acme",
                "size": "51-200", "industry": "Retail", "tags": ["shoes", "boots"], "about": "<p>Family firm</p>",
                "rating": 4.2},
        "hq": {"city": "Leeds", "state": "", "country": "UK"},
        "person": {"first": "Jane", "last": "Doe", "title": "CFO", "email": "JANE@ACME.COM", "status": "valid",
                   "li": "https://linkedin.com/in/jane", "mobile": "", "phone": "+44 1", "level": "c_suite",
                   "dept": "finance", "city": "York", "id": "p1"},
        "job": {"title": "AP <b>Clerk</b>", "date": "5 days ago", "url": "https://acme.com/jobs/1", "id": 77,
                "where": "Leeds", "text": "x " * 2000, "pay": "£30k"},
        "fund": {"stage": "seed", "amount": 2_000_000, "date": "2026-07-01"},
    }
    mapper = RecordMapper(mapping, label="lbl", today=TODAY, defaults={"signal_type": "job_posting"})
    c = mapper.to_company(rec)
    assert c.name == "Acme & Sons" and c.domain == "acme.com" and c.website == "https://www.acme.com/"
    assert c.linkedin_url == "https://linkedin.com/company/acme" and c.employees == 51
    assert c.industry == "Retail" and c.keywords == ["shoes", "boots"] and c.description == "Family firm"
    assert c.location == "Leeds, UK" and c.country == "UK" and c.data == {"rating": 4.2, "city": "Leeds"}
    [ct] = c.contacts
    assert (ct.full_name, ct.title, ct.email, ct.email_status) == ("Jane Doe", "CFO", "jane@acme.com", "valid")
    assert ct.phone == "+44 1" and ct.location == "York" and ct.data == {"source_id": "p1", "email_status_raw": "valid"} and ct.source == "lbl"
    job, fund = c.signals
    assert job.type == "job_posting" and job.title == "AP Clerk" and job.posted_at == date(2026, 9, 19)
    assert job.external_id == "77" and job.location == "Leeds" and len(job.description) <= 1500
    assert job.data == {"salary": "£30k"} and job.source == "lbl"
    assert fund.title == "Raised Seed ($2M)" and fund.posted_at == date(2026, 7, 1)
    assert c.sources == ["lbl"]


def test_record_mapper_edge_cases():
    mapper = RecordMapper({"name": "n", "website": "w", "email": "e", "signal_title": "t"}, label="x")
    assert mapper.to_company({"n": "", "w": ""}) is None
    assert mapper.to_company("not a dict") is None  # type: ignore[arg-type]
    c = mapper.to_company({"n": "", "w": "https://acme.org/x"})
    assert c.name == "acme.org"
    c = mapper.to_company({"n": "Solo", "e": "solo@gmail.com"})
    assert c.domain == "" and c.contacts[0].email == "solo@gmail.com"
    c = mapper.to_company({"n": "Sig", "t": "Something"})
    assert c.signals[0].type == SignalType.CUSTOM  # no type configured anywhere
    with pytest.raises(ValueError, match="signal_title_template"):
        RecordMapper({}, label="x", signal_title_template="Rated {score")
    with pytest.raises(ValueError, match="people"):
        RecordMapper({}, label="x", people={"mapping": {}})
    with pytest.raises(ValueError, match="person fields"):
        RecordMapper({}, label="x", people={"path": "team", "mapping": {"industry": "x"}})
    with pytest.raises(ValueError, match="default_signal"):
        RecordMapper({}, label="x", default_signal=42)


def test_people_list_and_signal_requires():
    rec = {"company": "Acme", "rating": None, "reviews": 3,
           "team": [{"name": "Ann Lee", "role": "CEO", "linkedin": "https://linkedin.com/in/ann"},
                    {"name": "", "role": "Nobody"}, "junk",
                    {"full_name": "Bob Ray", "email": "bob@acme.com", "email_status": "catch_all"}]}
    mapper = RecordMapper({"name": "company"}, label="x", people={"path": "team"},
                          signal_title_template="Rated {rating} from {reviews} reviews", signal_requires=["rating"])
    c = mapper.to_company(rec)
    assert [(p.full_name, p.title) for p in c.contacts] == [("Ann Lee", "CEO"), ("Bob Ray", "")]
    assert c.contacts[0].linkedin_url == "https://linkedin.com/in/ann"
    assert c.contacts[1].email_status == EmailStatus.RISKY
    assert c.domain == "acme.com"  # derived from the work email
    assert c.signals == []  # rating missing -> no review signal
    rec["rating"] = 4.5
    assert mapper.to_company(rec).signals[0].title == "Rated 4.5 from 3 reviews"


def test_records_to_companies_grouping_limit_and_default_signal(caplog):
    records = [
        {"Company": "Acme", "Site": "acme.com", "Job": "Buyer"},
        {"Company": "Acme Ltd", "Site": "www.acme.com", "Job": "Planner"},
        {"Company": "Beta", "Site": "", "Job": ""},
        {"Company": "", "Site": ""},
        {"Company": "Gamma", "Site": "gamma.io", "Job": "Ops"},
    ]
    with caplog.at_level(logging.INFO):
        companies = records_to_companies(
            records, {"name": "Company", "website": "Site", "signal_title": "Job"}, label="lst",
            defaults={"signal_type": "job_posting", "country": "Spain"}, today=TODAY, limit=2,
            default_signal={"type": "event", "title": "Listed on {Company} directory"},
            log=logging.getLogger("leadgen.test"))
    assert [c.name for c in companies] == ["Acme", "Beta"]
    acme, beta = companies
    assert [s.title for s in acme.signals] == ["Buyer", "Planner"] and acme.country == "Spain"
    assert beta.signals[0].type == "event" and beta.signals[0].title == "Listed on Beta directory"
    assert "skipped 1 of 5" in caplog.text and "dropped 1" in caplog.text


def test_location_from_signal_toggle():
    rec = {"n": "Acme", "t": "Clerk", "l": "Porto"}
    mapping = {"name": "n", "signal_title": "t", "signal_location": "l"}
    assert RecordMapper(mapping, label="x").to_company(rec).location == "Porto"
    assert RecordMapper(mapping, label="x", location_from_signal=False).to_company(rec).location == ""


# --- header detection ----------------------------------------------------------------------------------

def test_normalize_header():
    assert normalize_header("Company Name") == normalize_header("company_name") == "companyname"
    assert normalize_header("# Employees") == "#employees"


def test_detect_mapping_modes():
    m, info = detect_mapping(["Company Name", "Website", "Industry", "Employee Count", "Headquarters", "LinkedIn URL"])
    assert info["mode"] == "companies" and m["linkedin_url"] == ["LinkedIn URL"] and m["location"] == ["Headquarters"]
    assert m["employees"] == ["Employee Count"]

    m, info = detect_mapping(["Name", "Email", "Title", "Company", "LinkedIn"])
    assert info["mode"] == "people" and m["full_name"] == ["Name"] and m["title"] == ["Title"]
    assert m["person_linkedin_url"] == ["LinkedIn"] and "signal_title" not in m

    m, info = detect_mapping(["Name", "Website", "Description"])
    assert info["mode"] == "companies" and m["name"] == ["Name"] and m["description"] == ["Description"]

    m, info = detect_mapping(["job_title", "company", "job_url", "posted", "location", "description", "id"])
    assert info["mode"] == "jobs" and info["signal_type"] == "job_posting"
    assert m["signal_title"] == ["job_title"] and m["signal_url"] == ["job_url"] and m["signal_date"] == ["posted"]
    assert m["signal_description"] == ["description"] and m["signal_id"] == ["id"]
    assert m["location"] == ["location"] and m["signal_location"] == ["location"] and "description" not in m

    m, info = detect_mapping(["Company", "Vacancy", "Email", "Job Title"])
    assert info["mode"] == "people" and m["signal_title"] == ["Vacancy"] and m["title"] == ["Job Title"]


def test_resolve_columns(caplog):
    with caplog.at_level(logging.WARNING):
        out = resolve_columns({"name": "company NAME", "industry": ["Sector", "Industry"], "city": "hq.city"},
                              ["Company Name", "Industry"], log=logging.getLogger("leadgen.test"), label="csv")
    assert out == {"name": ["Company Name"], "industry": ["Sector", "Industry"], "city": ["hq.city"]}
    assert "'Sector': no such column" in caplog.text and "hq.city" not in caplog.text


# --- regressions (verifier/fixer pass) --------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    # negated "valid"/"deliverable" is a known-bad address, never VALID (verification would be skipped)
    ("Not valid", EmailStatus.INVALID), ("not_valid", EmailStatus.INVALID), ("NotValid", EmailStatus.INVALID),
    ("non-deliverable", EmailStatus.INVALID), ("Not deliverable", EmailStatus.INVALID),
    # pending / negated checks -> unknown, so the configured verifier still runs
    ("Validation pending", EmailStatus.UNKNOWN), ("Needs validation", EmailStatus.UNKNOWN),
    ("unvalidated", EmailStatus.UNKNOWN), ("Not Validated", EmailStatus.UNKNOWN), ("not ok", EmailStatus.UNKNOWN),
    ("verification required", EmailStatus.UNKNOWN),
    # positives keep working
    ("Email valid", EmailStatus.VALID), ("Verified - SMTP", EmailStatus.VALID),
    ("Valid (catch-all)", EmailStatus.RISKY),
])
def test_email_status_negations_and_pending_are_never_valid(raw, expected):
    assert normalize_email_status(raw) == expected


def test_record_with_not_valid_status_is_not_marked_valid():
    [c] = records_to_companies(
        [{"Company": "Acme Corp", "Website": "acme-demo.com", "First Name": "Jane", "Last Name": "Doe",
          "Email": "jane.doe@acme-demo.com", "Email Status": "Not valid"}],
        {"name": "Company", "website": "Website", "first_name": "First Name", "last_name": "Last Name",
         "email": "Email", "email_status": "Email Status"}, label="csv", today=TODAY)
    assert c.contacts[0].email_status == EmailStatus.INVALID


MAPPING_PEOPLE = {"name": "Company", "first_name": "First", "last_name": "Last", "email": "Email"}


def test_isp_and_regional_freemail_domains_never_become_company_domain():
    recs = [{"Company": "Bob's HVAC", "First": "Bob", "Last": "Smith", "Email": "bob@sbcglobal.net"},
            {"Company": "Joe's Plumbing", "First": "Joe", "Last": "Jones", "Email": "joe@sbcglobal.net"},
            {"Company": "Praxis Weber", "First": "Anna", "Last": "Weber", "Email": "praxis.weber@web.de"},
            {"Company": "Praxis Keller", "First": "Tom", "Last": "Keller", "Email": "praxis.keller@web.de"},
            {"Company": "Salon Lumi", "First": "Ana", "Last": "Lumi", "Email": "ana@yahoo.co.nz"}]
    companies = records_to_companies(recs, MAPPING_PEOPLE, label="csv", today=TODAY)
    assert [(c.name, c.domain, [ct.email for ct in c.contacts]) for c in companies] == [
        ("Bob's HVAC", "", ["bob@sbcglobal.net"]), ("Joe's Plumbing", "", ["joe@sbcglobal.net"]),
        ("Praxis Weber", "", ["praxis.weber@web.de"]), ("Praxis Keller", "", ["praxis.keller@web.de"]),
        ("Salon Lumi", "", ["ana@yahoo.co.nz"])]


def test_email_domain_unrelated_to_company_name_is_only_a_hint():
    # an unknown ISP shared by two businesses must not merge them into one company
    recs = [{"Company": "Bob's HVAC", "First": "Bob", "Last": "Smith", "Email": "bob@tri-county-isp.net"},
            {"Company": "Joe's Plumbing", "First": "Joe", "Last": "Jones", "Email": "joe@tri-county-isp.net"},
            {"Company": "Acme Corp", "First": "Jane", "Last": "Doe", "Email": "jane@acme-demo.com"},
            {"Company": "Blue Door Dental", "First": "Sam", "Last": "Poe", "Email": "sam@bluedoordental.co.uk"},
            {"Company": "International Business Machines", "First": "Al", "Last": "Bo", "Email": "al@ibm.com"}]
    bob, joe, acme, blue, ibm = records_to_companies(recs, MAPPING_PEOPLE, label="csv", today=TODAY)
    assert (bob.name, bob.domain, bob.data["email_domain"]) == ("Bob's HVAC", "", "tri-county-isp.net")
    assert (joe.name, joe.domain, [c.email for c in joe.contacts]) == ("Joe's Plumbing", "", ["joe@tri-county-isp.net"])
    assert acme.domain == "acme-demo.com" and blue.domain == "bluedoordental.co.uk" and ibm.domain == "ibm.com"
    # nameless rows still take the work-email domain as identity
    [c] = records_to_companies([{"Email": "jane@quantum-ink.com"}], {"email": "Email"}, label="csv")
    assert (c.name, c.domain) == ("quantum-ink.com", "quantum-ink.com")


GOOGLE_MAPS_KEYS = ["title", "totalScore", "reviewsCount", "street", "city", "countryCode", "website", "phone",
                    "categoryName", "url", "placeId"]


def test_detect_mapping_google_maps_title_is_the_business_name_not_a_job():
    m, info = detect_mapping(GOOGLE_MAPS_KEYS)
    assert m["name"] == ["title"] and "signal_title" not in m and info["mode"] == "companies"
    assert m["industry"] == ["categoryName"]
    # with an email column the business name is still not the person's title
    m, info = detect_mapping(GOOGLE_MAPS_KEYS + ["email"])
    assert m["name"] == ["title"] and "title" not in m and m["email"] == ["email"]
    # a plain job list without company column keeps reading 'title' as the job
    m, info = detect_mapping(["title", "website", "posted"])
    assert info["mode"] == "jobs" and m["signal_title"] == ["title"]


def test_detect_mapping_place_export_name_column_is_the_business():
    m, info = detect_mapping(["name", "site", "phone", "full_address", "email_1", "rating", "reviews"])
    assert m["name"] == ["name"] and "full_name" not in m and m["email"] == ["email_1"]
    [c] = records_to_companies([{"name": "Blue Door Dental", "site": "https://bluedoordental.co.uk",
                                 "email_1": "sarah.jones@bluedoordental.co.uk", "rating": 4.8, "reviews": 212}],
                               m, label="csv", today=TODAY)
    assert c.name == "Blue Door Dental" and c.domain == "bluedoordental.co.uk"
    [ct] = c.contacts
    assert (ct.first_name, ct.last_name, ct.email) == ("", "", "sarah.jones@bluedoordental.co.uk")
    # people lists keep 'Name' as the person
    m, _ = detect_mapping(["Name", "Email", "Title"])
    assert m["full_name"] == ["Name"] and "name" not in m
    m, _ = detect_mapping(["Name", "Email"])
    assert m["full_name"] == ["Name"]


def test_booking_and_listing_platform_pages_are_not_company_domains():
    assert clean_domain("https://www.doctolib.fr/dentiste/paris/luc-martin") == ""
    assert clean_domain("https://booksy.com/en-us/123_glow-salon") == ""
    assert clean_domain("https://calendly.com/acme-consulting/30min") == ""
    assert clean_domain("https://www.tripadvisor.co.uk/Restaurant_Review-g1-d2") == ""
    assert clean_domain("https://youtu.be/abc") == "" and clean_domain("https://lnkd.in/xyz") == ""
    # the platforms themselves stay usable as prospects
    assert clean_domain("https://calendly.com") == "calendly.com"
    assert clean_domain("https://www.doctolib.fr/") == "doctolib.fr" and clean_domain("doctolib.fr") == "doctolib.fr"
    assert clean_domain("https://www.bluedoordental.co.uk/booking/online") == "bluedoordental.co.uk"
    recs = [{"title": "Cabinet Dentaire Martin", "website": "https://www.doctolib.fr/dentiste/paris/luc-martin"},
            {"title": "Centre Dentaire Opera", "website": "https://www.doctolib.fr/centre-dentaire/paris/opera"}]
    a, b = records_to_companies(recs, {"name": "title", "website": "website"}, label="apify")
    assert (a.name, a.domain, a.website) == ("Cabinet Dentaire Martin", "", "")
    assert a.data["listing_url"] == "https://www.doctolib.fr/dentiste/paris/luc-martin"
    assert (b.name, b.domain) == ("Centre Dentaire Opera", "")


def test_bracketed_domain_values_do_not_raise():
    assert clean_domain("acme.com]") == "acme.com"
    assert clean_domain("[acme.com]") == "acme.com"
    assert clean_domain("https://[www.acme.com]") == "acme.com"
    assert clean_domain("[Acme](https://acme.com)") in ("", "acme.com")
    recs = [{"Company": "Weird Co", "Website": "[weirdco.com]"}, {"Company": "Odd Co", "Website": "https://[odd.io"},
            {"Company": "Good Co", "Website": "good.io"}]
    companies = records_to_companies(recs, {"name": "Company", "website": "Website"}, label="csv")
    assert [(c.name, c.domain) for c in companies] == [("Weird Co", "weirdco.com"), ("Odd Co", "odd.io"),
                                                        ("Good Co", "good.io")]


def test_numeric_dates_follow_the_files_day_month_order():
    assert parse_when("9/5/2026", TODAY, "mdy") == date(2026, 9, 5)
    assert parse_when("9/5/2026", TODAY, "dmy") == date(2026, 5, 9)
    assert parse_when("9/5/2026 14:03", TODAY, "mdy") == date(2026, 9, 5)
    assert parse_when("13/5/2026", TODAY, "mdy") is None
    assert parse_when("9/5/2026", TODAY) == date(2026, 5, 9)  # legacy default without evidence
    recs = [{"Company": "Fresh Co", "Date": "9/5/2026"}, {"Company": "Fresh Two", "Date": "9/20/2026"},
            {"Company": "Stale Co", "Date": "1/9/2026"}]
    mapping = {"name": "Company", "signal_title": "Company", "signal_date": "Date"}
    got = {c.name: c.signals[0].posted_at for c in records_to_companies(recs, mapping, label="csv", today=TODAY)}
    assert got == {"Fresh Co": date(2026, 9, 5), "Fresh Two": date(2026, 9, 20), "Stale Co": date(2026, 1, 9)}
    got = {c.name: c.signals[0].posted_at
           for c in records_to_companies(recs[:1], mapping, label="csv", today=TODAY, date_order="us")}
    assert got == {"Fresh Co": date(2026, 9, 5)}
    with pytest.raises(ValueError, match="date_order"):
        records_to_companies(recs, mapping, label="csv", date_order="ymd")


def test_first_name_plus_full_name_yields_last_name():
    mapper = RecordMapper({}, label="x", people={"path": "team", "mapping": {"full_name": "full_name",
                                                                             "first_name": "first_name"}})
    c = mapper.to_company({"team": [{"full_name": "Lena Vogel", "first_name": "Lena"},
                                    {"full_name": "Dr. Max Mustermann", "first_name": "Max"},
                                    {"full_name": "Cher", "first_name": "Cher"}]},
                          extra_defaults={"name": "Nordlicht"})
    assert [(ct.first_name, ct.last_name) for ct in c.contacts] == [("Lena", "Vogel"), ("Max", "Mustermann"),
                                                                    ("Cher", "")]


def test_detect_mapping_crm_rating_column_does_not_make_name_a_business():
    m, _ = detect_mapping(["Name", "Email", "Rating"])  # e.g. a CRM lead rating (Hot/Warm/Cold)
    assert m["full_name"] == ["Name"] and "name" not in m
