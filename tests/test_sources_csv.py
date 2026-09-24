"""Tests for the file sources (CsvSource / JsonSource)."""
from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

import pytest

from leadgen import registry
from leadgen.models import EmailStatus, SignalType
from leadgen.sources.csv_source import CsvSource, JsonSource, sniff_delimiter

APOLLO_PEOPLE_HEADERS = [
    "First Name", "Last Name", "Title", "Company", "Company Name for Emails", "Email", "Email Status",
    "Seniority", "Departments", "Work Direct Phone", "Mobile Phone", "Corporate Phone", "# Employees",
    "Industry", "Keywords", "Person Linkedin Url", "Website", "Company Linkedin Url", "City", "State",
    "Country", "Company City", "Company State", "Company Country", "Latest Funding",
    "Latest Funding Amount", "Last Raised At",
]


def write_csv(path: Path, headers: List[str], rows: List[List[Any]], delimiter: str = ",",
              encoding: str = "utf-8") -> Path:
    with open(path, "w", newline="", encoding=encoding) as f:
        w = csv.writer(f, delimiter=delimiter)
        w.writerow(headers)
        w.writerows(rows)
    return path


def make_source(cls, make_ctx, **config: Any):
    ctx = make_ctx()
    config.setdefault("type", cls.name)
    return cls(config, ctx)


@pytest.fixture
def apollo_people_csv(tmp_path: Path) -> Path:
    rows = [
        ["Jane", "Doe", "Chief Financial Officer", "Acme Robotics, Inc.", "Acme Robotics", "jane@acmerobotics.com",
         "Verified", "c_suite", "master_finance", "+1 555 0101", "", "+1 555 0100", "120",
         "Industrial Automation", "robotics, automation, warehouse", "http://www.linkedin.com/in/janedoe",
         "http://www.acmerobotics.com", "http://www.linkedin.com/company/acme-robotics", "Austin", "Texas",
         "United States", "Denver", "Colorado", "United States", "Series A", "12000000", "2026-08-15"],
        ["John", "Roe", "VP Finance", "Acme Robotics, Inc.", "Acme Robotics", "john@acmerobotics.com",
         "Extrapolated", "vp", "master_finance", "", "+1 555 0199", "+1 555 0100", "120",
         "Industrial Automation", "robotics, automation", "http://www.linkedin.com/in/johnroe",
         "http://www.acmerobotics.com", "http://www.linkedin.com/company/acme-robotics", "Denver", "Colorado",
         "United States", "Denver", "Colorado", "United States", "Series A", "12000000", "2026-08-15"],
        ["Maria", "Rossi", "Head of People", "Bella Foods Srl", "Bella Foods", "email_not_unlocked@domain.com",
         "Unavailable", "head", "master_human_resources", "", "", "", "45", "Food & Beverages", "",
         "http://www.linkedin.com/in/mariarossi", "https://bellafoods.it", "", "Milan", "Lombardy", "Italy",
         "Milan", "Lombardy", "Italy", "", "", ""],
        ["", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", ""],
    ]
    return write_csv(tmp_path / "apollo_people.csv", APOLLO_PEOPLE_HEADERS, rows)


# --- registry binding --------------------------------------------------------------

def test_registry_binds_file_sources(make_ctx, tmp_path):
    ctx = make_ctx()
    src = registry.create("source", {"type": "csv", "path": str(tmp_path / "x.csv")}, ctx)
    assert isinstance(src, CsvSource) and src.offline and src.name == "csv"
    src = registry.create("source", {"type": "json", "path": "x.json"}, ctx)
    assert isinstance(src, JsonSource) and src.offline and src.name == "json"


# --- Apollo people export -------------------------------------------------------------

def test_apollo_people_export_groups_people_by_company(make_ctx, apollo_people_csv):
    src = make_source(CsvSource, make_ctx, path=str(apollo_people_csv))
    companies = src.fetch()
    assert [c.name for c in companies] == ["Acme Robotics, Inc.", "Bella Foods Srl"]
    acme, bella = companies
    assert acme.domain == "acmerobotics.com"
    assert acme.linkedin_url == "http://www.linkedin.com/company/acme-robotics"
    assert acme.employees == 120 and acme.industry == "Industrial Automation"
    assert acme.keywords == ["robotics", "automation", "warehouse"]
    # company-prefixed columns are the company location; plain City/State/Country = the person
    assert acme.location == "Denver, Colorado, United States" and acme.country == "United States"
    assert acme.data["city"] == "Denver" and acme.data["phone"] == "+1 555 0100"
    assert acme.sources == ["csv"]

    assert [c.full_name for c in acme.contacts] == ["Jane Doe", "John Roe"]
    jane, john = acme.contacts
    assert jane.title == "Chief Financial Officer" and jane.email == "jane@acmerobotics.com"
    assert jane.email_status == EmailStatus.VALID and jane.seniority == "c_suite"
    assert jane.department == "master_finance" and jane.phone == "+1 555 0101"
    assert jane.linkedin_url == "http://www.linkedin.com/in/janedoe"
    assert jane.location == "Austin, Texas, United States" and jane.source == "csv"
    assert john.email_status == EmailStatus.UNKNOWN and john.phone == "+1 555 0199"

    # funding columns -> one deduped funding signal
    assert len(acme.signals) == 1
    sig = acme.signals[0]
    assert sig.type == SignalType.FUNDING and sig.title == "Raised Series A ($12M)"
    assert sig.posted_at == date(2026, 8, 15) and sig.source == "csv"

    # Apollo placeholder email is dropped; the person is still a contact
    maria = bella.contacts[0]
    assert maria.email == "" and maria.email_status == EmailStatus.UNKNOWN and maria.title == "Head of People"
    assert bella.domain == "bellafoods.it" and bella.signals == []


def test_apollo_company_export_without_prefixed_location(make_ctx, tmp_path):
    headers = ["Company", "# Employees", "Industry", "Website", "Company Linkedin Url", "City", "State",
               "Country", "Short Description", "Keywords", "Latest Funding", "Latest Funding Amount",
               "Last Raised At", "Total Funding"]
    rows = [["Nordlicht GmbH", "85", "Software", "nordlicht.de", "", "Berlin", "", "Germany",
             "<p>We build &amp; run <b>fleet</b> software.</p>", "saas; fleet", "seed", "$2.5M", "3 days ago", "4000000"]]
    path = write_csv(tmp_path / "companies.csv", headers, rows)
    [c] = make_source(CsvSource, make_ctx, path=str(path)).fetch()
    assert c.location == "Berlin, Germany" and c.country == "Germany"
    assert c.description == "We build & run fleet software."
    assert c.keywords == ["saas", "fleet"] and c.domain == "nordlicht.de" and c.contacts == []
    [sig] = c.signals
    assert sig.title == "Raised Seed ($2.5M)" and sig.posted_at == date(2026, 9, 21)
    assert sig.data["total"] == 4000000 and "Total funding to date: $4M" in sig.description


# --- Sales Navigator / Clay style -------------------------------------------------------

def test_sales_navigator_clay_style(make_ctx, tmp_path):
    headers = ["Full Name", "Job Title", "Company Name", "Company Domain", "LinkedIn URL", "Employee Count",
               "Headquarters", "Location", "Work Email"]
    rows = [
        ["Ana García", "Head of Operations", "Logística Sur SL", "logisticasur.es",
         "https://www.linkedin.com/in/anagarcia", "51-200", "Seville, Spain", "Madrid, Spain",
         "ana@logisticasur.es"],
        ["Tom Baker", "COO", "Logística Sur SL", "https://www.logisticasur.es/", "", "51-200",
         "Seville, Spain", "Seville, Spain", "tom@logisticasur.es"],
    ]
    path = write_csv(tmp_path / "salesnav.csv", headers, rows)
    [c] = make_source(CsvSource, make_ctx, path=str(path)).fetch()
    assert c.name == "Logística Sur SL" and c.domain == "logisticasur.es"
    assert c.employees == 51 and c.location == "Seville, Spain"
    assert c.linkedin_url == ""  # 'LinkedIn URL' is the person's profile when people are present
    ana, tom = c.contacts
    assert (ana.first_name, ana.last_name) == ("Ana", "García")
    assert ana.title == "Head of Operations"  # person title, not a signal
    assert ana.linkedin_url == "https://www.linkedin.com/in/anagarcia" and ana.location == "Madrid, Spain"
    assert tom.email == "tom@logisticasur.es"
    assert c.signals == []


# --- job lists ---------------------------------------------------------------------------

def test_job_list_title_is_a_signal_with_relative_dates(make_ctx, tmp_path):
    headers = ["Job Title", "Employer", "Location", "Date Posted", "Job URL", "Description"]
    rows = [
        ["Senior Accountant", "Harbor Freight Partners", "Rotterdam", "3 days ago", "https://jobs.example/1",
         "<p>Join our <strong>finance</strong> team&nbsp;now</p>"],
        ["Payroll Specialist", "Harbor Freight Partners", "Rotterdam", "30+ days ago", "https://jobs.example/2", ""],
        ["Warehouse Lead", "Kestrel Logistics", "Lyon", "2026-09-01", "https://jobs.example/3", ""],
    ]
    path = write_csv(tmp_path / "jobs.csv", headers, rows)
    companies = make_source(CsvSource, make_ctx, path=str(path)).fetch()
    assert [c.name for c in companies] == ["Harbor Freight Partners", "Kestrel Logistics"]
    harbor = companies[0]
    assert harbor.location == "Rotterdam" and harbor.contacts == []
    s1, s2 = harbor.signals
    assert s1.type == SignalType.JOB_POSTING and s1.title == "Senior Accountant"
    assert s1.posted_at == date(2026, 9, 21) and s1.url == "https://jobs.example/1"
    assert s1.location == "Rotterdam" and s1.description == "Join our finance team now"
    assert s2.posted_at == date(2026, 8, 25)
    assert companies[1].signals[0].posted_at == date(2026, 9, 1)


def test_job_list_with_posted_and_role_columns(make_ctx, tmp_path):
    path = write_csv(tmp_path / "jobs2.csv", ["Company", "Role", "Posted", "Website"],
                     [["Quill Studio", "Office Manager", "today", "quill.studio"]])
    [c] = make_source(CsvSource, make_ctx, path=str(path)).fetch()
    assert c.signals[0].title == "Office Manager" and c.signals[0].posted_at == date(2026, 9, 24)
    assert c.domain == "quill.studio"


def test_signal_type_override_and_signal_type_column(make_ctx, tmp_path):
    path = write_csv(tmp_path / "news.csv", ["Company", "Title", "Signal Type"],
                     [["Acme", "Opened a Lisbon office", "Expansion"], ["Beta", "New CEO appointed", ""]])
    companies = make_source(CsvSource, make_ctx, path=str(path), signal_type="news").fetch()
    assert companies[0].signals[0].type == "expansion"
    assert companies[1].signals[0].type == "news"


# --- delimiters / encodings ---------------------------------------------------------------

@pytest.mark.parametrize("delimiter", [";", "\t", "|"])
def test_delimiter_is_sniffed(make_ctx, tmp_path, delimiter):
    path = write_csv(tmp_path / "d.csv", ["Company Name", "Website", "Industry"],
                     [["Acme, Inc.", "acme.com", "Retail; Online"]], delimiter=delimiter)
    [c] = make_source(CsvSource, make_ctx, path=str(path)).fetch()
    assert c.name == "Acme, Inc." and c.domain == "acme.com" and c.industry == "Retail; Online"


def test_sniff_delimiter_ignores_quoted_commas():
    assert sniff_delimiter('"Name, Legal";Website;Size\nA;b;c') == ";"
    assert sniff_delimiter("single\nvalue") == ","


def test_explicit_delimiter_names(make_ctx, tmp_path):
    path = write_csv(tmp_path / "t.tsv", ["Company", "Website"], [["Acme", "acme.com"]], delimiter="\t")
    [c] = make_source(CsvSource, make_ctx, path=str(path), delimiter="tab").fetch()
    assert c.domain == "acme.com"
    with pytest.raises(ValueError, match="single character"):
        make_source(CsvSource, make_ctx, path=str(path), delimiter="::").fetch()


def test_bom_and_cp1252_fallback(make_ctx, tmp_path):
    p1 = tmp_path / "bom.csv"
    p1.write_bytes("﻿Company,Website\nZürich Labs,zurichlabs.ch\n".encode("utf-8"))
    [c] = make_source(CsvSource, make_ctx, path=str(p1)).fetch()
    assert c.name == "Zürich Labs"
    p2 = tmp_path / "excel.csv"
    p2.write_bytes("Company;Website\nCafé Noël;cafenoel.fr\n".encode("cp1252"))
    [c] = make_source(CsvSource, make_ctx, path=str(p2)).fetch()
    assert c.name == "Café Noël" and c.domain == "cafenoel.fr"


def test_explicit_bad_encoding_is_a_clear_error(make_ctx, tmp_path):
    p = tmp_path / "excel.csv"
    p.write_bytes("Company\nCafé\n".encode("cp1252"))
    with pytest.raises(ValueError, match="cannot decode"):
        make_source(CsvSource, make_ctx, path=str(p), encoding="utf-8").fetch()
    with pytest.raises(ValueError, match="unknown encoding"):
        make_source(CsvSource, make_ctx, path=str(p), encoding="nope-42").fetch()


# --- mapping overrides / defaults / default_signal / limit ---------------------------------

def test_mapping_overrides_win_and_can_disable_fields(make_ctx, tmp_path):
    headers = ["Org", "Homepage", "Opening", "Where", "Industry"]
    rows = [["Pixel Forge", "pixelforge.io", "Game Designer", "Kraków", "Games"]]
    path = write_csv(tmp_path / "custom.csv", headers, rows)
    [c] = make_source(CsvSource, make_ctx, path=str(path),
                      mapping={"name": "org", "website": "Homepage", "signal_location": "WHERE",
                               "location": "Where", "industry": None},
                      defaults={"country": "Poland"}).fetch()
    assert c.name == "Pixel Forge" and c.domain == "pixelforge.io"
    assert c.industry == ""  # disabled
    assert c.country == "Poland" and c.location == "Kraków"
    assert c.signals[0].title == "Game Designer" and c.signals[0].location == "Kraków"


def test_mapping_to_missing_column_warns(make_ctx, tmp_path, caplog):
    path = write_csv(tmp_path / "c.csv", ["Company"], [["Acme"]])
    with caplog.at_level("WARNING"):
        [c] = make_source(CsvSource, make_ctx, path=str(path), mapping={"industry": "Sector Name"}).fetch()
    assert c.industry == "" and "no such column" in caplog.text


def test_unknown_mapping_field_raises(make_ctx, tmp_path):
    path = write_csv(tmp_path / "c.csv", ["Company"], [["Acme"]])
    with pytest.raises(ValueError, match="unknown mapping field 'colour'"):
        make_source(CsvSource, make_ctx, path=str(path), mapping={"colour": "x"}).fetch()


def test_default_signal_for_static_lists(make_ctx, tmp_path):
    path = write_csv(tmp_path / "attendees.csv", ["Company", "Website", "Booth"],
                     [["Acme", "acme.com", "B12"], ["Beta", "beta.io", ""]])
    companies = make_source(CsvSource, make_ctx, path=str(path),
                            default_signal={"type": "event", "title": "Exhibiting at Expo 2026 (booth {Booth})",
                                            "date": "2026-09-20"}).fetch()
    assert companies[0].signals[0].type == "event"
    assert companies[0].signals[0].title == "Exhibiting at Expo 2026 (booth B12)"
    assert companies[0].signals[0].posted_at == date(2026, 9, 20)
    # empty template fields render as '' and the leftover punctuation is tidied
    assert companies[1].signals[0].title == "Exhibiting at Expo 2026 (booth)"


def test_default_signal_requires_title(make_ctx, tmp_path):
    path = write_csv(tmp_path / "c.csv", ["Company"], [["Acme"]])
    with pytest.raises(ValueError, match="default_signal needs a 'title'"):
        make_source(CsvSource, make_ctx, path=str(path), default_signal={"type": "event"}).fetch()


def test_signal_title_template(make_ctx, tmp_path):
    path = write_csv(tmp_path / "t.csv", ["Company", "Role", "City"], [["Acme", "Buyer", "Leeds"]])
    [c] = make_source(CsvSource, make_ctx, path=str(path), mapping={"signal_title": None},
                      signal_title_template="Hiring a {Role} in {City}").fetch()
    assert c.signals[0].title == "Hiring a Buyer in Leeds"


def test_limit_caps_companies_but_keeps_grouping(make_ctx, tmp_path):
    rows = [["Acme", "acme.com", "Role A"], ["Beta", "beta.com", "Role B"], ["Acme", "acme.com", "Role C"],
            ["Gamma", "gamma.com", "Role D"]]
    path = write_csv(tmp_path / "l.csv", ["Company", "Website", "Job Title"], rows)
    companies = make_source(CsvSource, make_ctx, path=str(path), limit=2).fetch()
    assert [c.name for c in companies] == ["Acme", "Beta"]
    assert [s.title for s in companies[0].signals] == ["Role A", "Role C"]


def test_grouping_by_name_when_domain_missing_and_split_on_different_domains(make_ctx, tmp_path):
    rows = [["Apex Ltd", "", "Role A"], ["Apex", "apex.com", "Role B"], ["Apex", "apex.io", "Role C"]]
    path = write_csv(tmp_path / "g.csv", ["Company", "Website", "Job Title"], rows)
    companies = make_source(CsvSource, make_ctx, path=str(path)).fetch()
    assert len(companies) == 2
    assert companies[0].domain == "apex.com" and len(companies[0].signals) == 2
    assert companies[1].domain == "apex.io"


def test_rows_without_company_are_skipped_and_domain_from_work_email(make_ctx, tmp_path):
    rows = [["", "", "Jane Doe", "jane@quantum-ink.com"], ["", "", "Joe Bloggs", "joe@gmail.com"],
            ["", "", "", ""]]
    path = write_csv(tmp_path / "p.csv", ["Company", "Website", "Full Name", "Email"], rows)
    [c] = make_source(CsvSource, make_ctx, path=str(path)).fetch()
    assert c.domain == "quantum-ink.com" and c.name == "quantum-ink.com"
    assert c.contacts[0].email == "jane@quantum-ink.com"


def test_linkedin_website_is_not_a_domain(make_ctx, tmp_path):
    rows = [["Acme", "https://www.linkedin.com/company/acme"], ["Beta", "https://www.linkedin.com/company/beta"]]
    path = write_csv(tmp_path / "li.csv", ["Company Name", "Website"], rows)
    acme, beta = make_source(CsvSource, make_ctx, path=str(path)).fetch()
    assert acme.domain == "" and acme.website == ""
    assert acme.linkedin_url == "https://www.linkedin.com/company/acme"
    assert beta.name == "Beta"  # not merged with Acme via 'linkedin.com'


def test_skip_rows_and_duplicate_headers(make_ctx, tmp_path):
    p = tmp_path / "s.csv"
    p.write_text("Exported on 2026-09-24\n\nCompany,Website,Website\nAcme,acme.com,other.com\n", encoding="utf-8")
    [c] = make_source(CsvSource, make_ctx, path=str(p), skip_rows=1).fetch()
    assert c.domain == "acme.com"
    headers, _ = make_source(CsvSource, make_ctx, path=str(p), skip_rows=1).read_rows()
    assert headers == ["Company", "Website", "Website (2)"]


def test_short_and_long_rows(make_ctx, tmp_path):
    p = tmp_path / "r.csv"
    p.write_text("Company,Website,Industry\nAcme,acme.com\nBeta,beta.com,Retail,EXTRA\n", encoding="utf-8")
    acme, beta = make_source(CsvSource, make_ctx, path=str(p)).fetch()
    assert acme.industry == "" and beta.industry == "Retail"


def test_description_is_truncated(make_ctx, tmp_path):
    path = write_csv(tmp_path / "long.csv", ["Job Title", "Company", "Description"],
                     [["Analyst", "Acme", "word " * 1000]])
    [c] = make_source(CsvSource, make_ctx, path=str(path)).fetch()
    assert len(c.signals[0].description) <= 1500 and c.signals[0].description.endswith("…")


def test_label_is_used_for_sources_signals_and_contacts(make_ctx, tmp_path):
    path = write_csv(tmp_path / "x.csv", ["Company", "Job Title", "Email", "Full Name"],
                     [["Acme", "CFO", "a@acme.com", "Al Smith"]])
    [c] = make_source(CsvSource, make_ctx, path=str(path), label="conference-list",
                      default_signal={"title": "Met at expo"}).fetch()
    assert c.sources == ["conference-list"]
    assert c.signals[0].source == "conference-list" and c.contacts[0].source == "conference-list"


# --- errors ------------------------------------------------------------------------------------

def test_missing_path_and_missing_file(make_ctx, tmp_path):
    with pytest.raises(ValueError, match="'path' is required"):
        make_source(CsvSource, make_ctx).fetch()
    with pytest.raises(FileNotFoundError, match="file not found"):
        make_source(CsvSource, make_ctx, path=str(tmp_path / "nope.csv")).fetch()
    with pytest.raises(ValueError, match="is a directory"):
        make_source(CsvSource, make_ctx, path=str(tmp_path)).fetch()


def test_empty_file_returns_nothing(make_ctx, tmp_path):
    p = tmp_path / "empty.csv"
    p.write_text("", encoding="utf-8")
    assert make_source(CsvSource, make_ctx, path=str(p)).fetch() == []
    p.write_text("Company,Website\n", encoding="utf-8")
    assert make_source(CsvSource, make_ctx, path=str(p)).fetch() == []


def test_path_resolves_relative_to_playbook(make_ctx, tmp_path, monkeypatch):
    data_dir = tmp_path / "pb"
    data_dir.mkdir()
    write_csv(data_dir / "leads.csv", ["Company", "Website"], [["Acme", "acme.com"]])
    ctx = make_ctx()
    ctx.playbook.path = data_dir / "playbook.yaml"
    monkeypatch.chdir(tmp_path)
    [c] = CsvSource({"type": "csv", "path": "leads.csv"}, ctx).fetch()
    assert c.domain == "acme.com"


def test_csv_source_makes_no_http_calls_and_runs_in_dry_run(make_ctx, apollo_people_csv):
    ctx = make_ctx(dry_run=True)
    companies = CsvSource({"type": "csv", "path": str(apollo_people_csv)}, ctx).fetch()
    assert companies and ctx.http.calls == []


# --- JSON -------------------------------------------------------------------------------------

def _json_records() -> List[Dict[str, Any]]:
    return [
        {"company": {"name": "Acme Robotics", "domain": "acmerobotics.com", "size": 120},
         "job": {"title": "Controller", "url": "https://acme.jobs/1", "posted": "2 days ago", "id": 991},
         "hq": {"city": "Denver", "country": "US"},
         "team": [{"name": "Jane Doe", "role": "CFO", "linkedin": "https://linkedin.com/in/jane"}]},
        {"company": {"name": "Acme Robotics", "domain": "acmerobotics.com"},
         "job": {"title": "AP Clerk", "url": "https://acme.jobs/2", "posted": "2026-09-01", "id": 992}},
        {"company": {"name": ""}, "job": {"title": "No company"}},
        "not-a-dict",
    ]


JSON_MAPPING = {"name": "company.name", "domain": "company.domain", "employees": "company.size",
                "city": "hq.city", "country": "hq.country", "signal_title": "job.title",
                "signal_url": "job.url", "signal_date": "job.posted", "signal_id": "job.id"}


@pytest.mark.parametrize("layout", ["array", "items", "jsonl", "records_path"])
def test_json_layouts_with_dotted_mapping(make_ctx, tmp_path, layout):
    records = _json_records()
    extra: Dict[str, Any] = {}
    if layout == "array":
        p = tmp_path / "data.json"
        p.write_text(json.dumps(records), encoding="utf-8")
    elif layout == "items":
        p = tmp_path / "data.json"
        p.write_text(json.dumps({"items": records, "count": 4}), encoding="utf-8")
    elif layout == "jsonl":
        p = tmp_path / "data.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    else:
        p = tmp_path / "data.json"
        p.write_text(json.dumps({"result": {"page": {"entries": records}}}), encoding="utf-8")
        extra["records_path"] = "result.page.entries"
    src = make_source(JsonSource, make_ctx, path=str(p), mapping=JSON_MAPPING,
                      people={"path": "team", "mapping": {"linkedin_url": "linkedin"}}, **extra)
    [c] = src.fetch()
    assert c.name == "Acme Robotics" and c.domain == "acmerobotics.com" and c.employees == 120
    assert c.location == "Denver, US" and c.country == "US" and c.sources == ["json"]
    assert [s.title for s in c.signals] == ["Controller", "AP Clerk"]
    s1 = c.signals[0]
    assert s1.type == SignalType.JOB_POSTING and s1.external_id == "991"
    assert s1.posted_at == date(2026, 9, 22) and s1.url == "https://acme.jobs/1"
    [jane] = c.contacts
    assert jane.full_name == "Jane Doe" and jane.title == "CFO" and jane.linkedin_url == "https://linkedin.com/in/jane"
    assert jane.source == "json"


def test_json_auto_detects_flat_keys(make_ctx, tmp_path):
    p = tmp_path / "flat.json"
    p.write_text(json.dumps([
        {"companyName": "Kestrel Logistics", "website": "https://kestrel.fr", "title": "Warehouse Lead",
         "location": "Lyon", "postedAt": "2026-09-20", "url": "https://jobs.example/k1", "id": "k1"},
    ]), encoding="utf-8")
    [c] = make_source(JsonSource, make_ctx, path=str(p)).fetch()
    assert c.name == "Kestrel Logistics" and c.domain == "kestrel.fr" and c.location == "Lyon"
    s = c.signals[0]
    assert (s.title, s.url, s.external_id, s.posted_at) == ("Warehouse Lead", "https://jobs.example/k1", "k1",
                                                            date(2026, 9, 20))


def test_json_single_object_and_errors(make_ctx, tmp_path):
    p = tmp_path / "one.json"
    p.write_text(json.dumps({"company": "Acme", "website": "acme.com"}), encoding="utf-8")
    [c] = make_source(JsonSource, make_ctx, path=str(p)).fetch()
    assert c.domain == "acme.com"

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        make_source(JsonSource, make_ctx, path=str(bad)).fetch()

    badl = tmp_path / "bad.jsonl"
    badl.write_text('{"company": "A"}\n{oops\n', encoding="utf-8")
    with pytest.raises(ValueError, match="line 2"):
        make_source(JsonSource, make_ctx, path=str(badl)).fetch()

    p2 = tmp_path / "rp.json"
    p2.write_text(json.dumps({"data": {"x": 1}}), encoding="utf-8")
    with pytest.raises(ValueError, match="records_path"):
        make_source(JsonSource, make_ctx, path=str(p2), records_path="data.x").fetch()

    scalar = tmp_path / "scalar.json"
    scalar.write_text("42", encoding="utf-8")
    with pytest.raises(ValueError, match="expected a JSON array or object"):
        make_source(JsonSource, make_ctx, path=str(scalar)).fetch()


def test_json_empty_file_and_limit(make_ctx, tmp_path):
    p = tmp_path / "empty.json"
    p.write_text("  ", encoding="utf-8")
    assert make_source(JsonSource, make_ctx, path=str(p)).fetch() == []
    p.write_text(json.dumps([{"company": f"Co {i}", "website": f"co{i}.com"} for i in range(10)]), encoding="utf-8")
    assert len(make_source(JsonSource, make_ctx, path=str(p), limit=3).fetch()) == 3


def test_json_multiline_jsonl_without_extension(make_ctx, tmp_path):
    p = tmp_path / "export.json"
    p.write_text('{"company": "A", "website": "a.com"}\n{"company": "B", "website": "b.com"}\n', encoding="utf-8")
    assert [c.name for c in make_source(JsonSource, make_ctx, path=str(p)).fetch()] == ["A", "B"]
