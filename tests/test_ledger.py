"""Delivery ledger: item keys, delivered history, per-client suppression and the pipeline hooks."""
from __future__ import annotations

import csv
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import List

import pytest

from leadgen import registry
from leadgen.context import Context
from leadgen.delivery.client import Client, client_playbook
from leadgen.delivery.ledger import (
    DELIVERY_KINDS,
    Ledger,
    LedgerHooks,
    company_item_key,
    contact_item_key,
    contact_item_keys,
    domain_and_parents,
    guess_kind,
    job_item_key,
    lead_items,
    linkedin_key,
    normalize_job_url,
)
from leadgen.enrich.base import ContactFinder
from leadgen.models import Company, Contact, Lead, Signal
from leadgen.pipeline import Pipeline, PipelineHooks
from leadgen.store import Store
from tests.conftest import TODAY
from tests.fakes import FakeHttp

LAST_WEEK = TODAY - timedelta(days=7)


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


@pytest.fixture
def ledger(store):
    return Ledger(store)


def company(name="Acme Corp", domain="acme.com", *signals: Signal) -> Company:
    return Company(name=name, domain=domain, signals=list(signals))


def job(title="Senior Accountant", ext="", url="", type_="job_posting", posted="2026-09-22") -> Signal:
    return Signal(type=type_, title=title, external_id=ext, url=url, posted_at=posted)


# --- item keys ---------------------------------------------------------------------------------

def test_company_item_key_is_the_company_key():
    assert company_item_key(company()) == "acme.com"
    assert company_item_key(Company(name="The Acme Group, Inc.")) == "name:acme"


def test_job_item_key_prefers_external_id_then_url_then_fingerprint():
    c = company()
    assert job_item_key(c, job(ext="gh-123", url="https://acme.com/jobs/9")) == "acme.com|gh-123"
    assert job_item_key(c, job(url="https://www.Acme.com/jobs/9/?utm_source=li&utm_medium=x#apply")) == \
        "acme.com/jobs/9"
    assert job_item_key(c, job()) == "acme.com|job_posting:senior accountant"
    # the fingerprint fallback is per company: the same title elsewhere is another job
    assert job_item_key(company("Beta", "beta.com"), job()) != job_item_key(c, job())


def test_job_url_keeps_parameters_that_identify_the_job():
    a = normalize_job_url("https://www.indeed.com/viewjob?jk=abc123&from=serp&utm_campaign=x")
    b = normalize_job_url("http://indeed.com/viewjob?jk=def456&from=serp")
    assert a == "indeed.com/viewjob?jk=abc123" and b == "indeed.com/viewjob?jk=def456"
    assert normalize_job_url("https://boards.greenhouse.io/acme/jobs/55?gh_src=abc&b=2&a=1") == \
        "boards.greenhouse.io/acme/jobs/55?a=1&b=2"
    assert normalize_job_url("https://boards.greenhouse.io/acme?gh_jid=4012345&gh_src=x") == \
        "boards.greenhouse.io/acme?gh_jid=4012345"                    # gh_jid names the job: kept
    assert normalize_job_url("acme.com/careers/acct/") == "acme.com/careers/acct"
    assert normalize_job_url("https://acme.com:8443/jobs/1") == "acme.com:8443/jobs/1"
    assert normalize_job_url("") == "" and normalize_job_url("   ") == ""
    assert normalize_job_url("http://[broken/jobs/1#x") == "http://[broken/jobs/1"   # never raises


@pytest.mark.parametrize("tracked", [
    "https://boards.greenhouse.io/acme/jobs/4012345?gh_src=linkedin",
    "https://boards.greenhouse.io/acme/jobs/4012345?gh_src=indeed&utm_source=x",
    "https://jobs.lever.co/acme/4012345?lever-source=LinkedIn&lever-origin=applied",
    "https://jobs.lever.co/acme/4012345?lever-source%5B%5D=Indeed&lever-via=abc",
    "https://careers-acme.icims.com/jobs/4012345/job?iis=Job+Board&iisn=LinkedIn",
    "https://acme.com/jobs/4012345?_gl=1*abc*_ga*xyz&_ga=2.1.2",
])
def test_ats_tracking_parameters_do_not_make_a_new_job(tracked):
    """The same ad reached through another source-tracking link is the same job."""
    clean = tracked.split("?", 1)[0]
    assert normalize_job_url(tracked) == normalize_job_url(clean)


def test_same_ad_with_another_tracking_link_is_not_delivered_again(ledger):
    first = job(url="https://boards.greenhouse.io/acme/jobs/4012345?gh_src=linkedin")
    ledger.record("acme", lead_items(Lead(company=company("Acme Corp", "acme.com", first))), "r1", LAST_WEEK)
    hooks = LedgerHooks(ledger, Client("acme", dedupe=["job", "contact"]), TODAY)
    again = job(url="https://boards.greenhouse.io/acme/jobs/4012345?gh_src=indeed")
    assert hooks.filter_company(company("Acme Corp", "acme.com", again)) == "all its jobs were already delivered"


def test_contact_item_keys():
    c = company()
    full = Contact(first_name="Jane", last_name="Doe", email="Jane.Doe@Acme.com",
                   linkedin_url="https://www.linkedin.com/in/JaneDoe/?trk=x")
    assert contact_item_key(full) == "jane.doe@acme.com"
    assert contact_item_keys(full, c) == ["jane.doe@acme.com", "linkedin.com/in/janedoe", "jane doe|acme.com"]
    li_only = Contact(full_name="Jane Doe", linkedin_url="http://linkedin.com/in/janedoe")
    assert contact_item_key(li_only, c) == "linkedin.com/in/janedoe"
    name_only = Contact(first_name="José", last_name="Núñez")
    assert contact_item_key(name_only, c) == "jose nunez|acme.com"
    assert contact_item_key(name_only) == "jose nunez|"
    assert contact_item_key(Contact()) == "" and contact_item_keys(Contact(), c) == []


def test_lead_items_cover_company_jobs_and_every_contact_identity():
    c = company("Acme Corp", "acme.com", job(ext="1"), job("Controller", url="https://acme.com/j/2"),
                job("Series B", type_="funding"))
    lead = Lead(company=c, contact=Contact(first_name="Jane", last_name="Doe", email="jane@acme.com"))
    assert lead_items(lead) == [
        ("company", "acme.com"), ("job", "acme.com|1"), ("job", "acme.com/j/2"),
        ("job", "acme.com|funding:series b"),
        ("contact", "jane@acme.com"), ("contact", "jane doe|acme.com"),
        # the same company, jobs and person keyed by the name (for when the domain is missing next time)
        ("company_by_name", "name:acme"), ("job_by_name", "name:acme|1"),
        ("job_by_name", "name:acme|funding:series b"), ("contact_by_name", "jane doe|name:acme"),
    ]
    assert lead_items(Lead(company=company())) == [("company", "acme.com"), ("company_by_name", "name:acme")]
    # a company without a domain: its keys already are name keys
    assert lead_items(Lead(company=company("Acme Corp", "", job(ext="1")))) == [
        ("company", "name:acme"), ("job", "name:acme|1")]


def test_helpers_guess_kind_and_parent_domains():
    assert guess_kind("Jane@Acme.com") == "email"
    assert guess_kind("https://www.linkedin.com/in/jane") == "linkedin"
    assert guess_kind("https://www.acme.com/about") == "domain" and guess_kind("acme.co.uk") == "domain"
    assert guess_kind("Acme Inc") == "company" and guess_kind("Acme") == "company"
    assert domain_and_parents("eu.jobs.acme.co.uk") == ["eu.jobs.acme.co.uk", "jobs.acme.co.uk",
                                                        "acme.co.uk", "co.uk"]
    assert domain_and_parents("jane@acme.com") == ["acme.com"]
    assert domain_and_parents("") == []


# --- the ledger ----------------------------------------------------------------------------------

def _tables(store: Store) -> List[str]:
    return [r["name"] for r in store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]


def test_tables_are_created_lazily_in_the_store_file(store):
    ledger = Ledger(store)
    assert "deliveries" not in _tables(store) and "client_suppression" not in _tables(store)
    assert ledger.delivered("acme", "company", "acme.com") is False
    assert {"deliveries", "client_suppression"} <= set(_tables(store))


def test_record_and_delivered_ever(ledger):
    n = ledger.record("acme", [("company", "acme.com"), ("job", "acme.com|1"), ("job", "acme.com|1"),
                               ("contact", ""), ("contact", "jane@acme.com")], run_id="r1", today=LAST_WEEK)
    assert n == 3   # duplicates and empty keys are not counted
    assert ledger.delivered("acme", "company", "acme.com")
    assert ledger.delivered("acme", "job", "acme.com|1", None, TODAY)
    assert not ledger.delivered("acme", "job", "acme.com|2")
    assert not ledger.delivered("acme", "contact", "acme.com")       # kinds are separate
    assert not ledger.delivered("other-client", "company", "acme.com")  # clients are separate
    assert not ledger.delivered("acme", "company", "")
    assert ledger.delivered_on("acme", "company", "acme.com") == LAST_WEEK


def test_redelivery_window_is_measured_in_days_before_today(ledger):
    ledger.record("acme", [("company", "a.com")], "r1", today=date(2026, 9, 17))
    ledger.record("acme", [("company", "b.com")], "r1", today=date(2026, 9, 18))
    # a.com was delivered exactly 7 days ago: allowed again with a 7-day window
    assert not ledger.delivered("acme", "company", "a.com", 7, TODAY)
    assert ledger.delivered("acme", "company", "a.com", 8, TODAY)
    assert ledger.delivered("acme", "company", "b.com", 7, TODAY)          # 6 days ago
    assert ledger.delivered("acme", "company", "a.com", None, TODAY)       # None = ever
    assert not ledger.delivered("acme", "company", "a.com", 0, TODAY)      # 0 = nothing blocks
    assert ledger.delivered_on("acme", "company", "b.com", 7, TODAY) == date(2026, 9, 18)


def test_record_is_idempotent_and_moves_items_to_the_latest_delivery(ledger, store):
    ledger.record("acme", [("company", "acme.com")], "r1", today=LAST_WEEK)
    assert ledger.record("acme", [("company", "acme.com")], "r2", today=TODAY) == 1
    rows = store.conn.execute("SELECT run_id, delivered_at FROM deliveries").fetchall()
    assert [(r["run_id"], r["delivered_at"]) for r in rows] == [("r2", TODAY.isoformat())]
    assert ledger.delivered("acme", "company", "acme.com", 3, TODAY)


def test_bad_kind_and_client_are_rejected(ledger):
    with pytest.raises(ValueError, match="delivery kind must be one of company, job, contact"):
        ledger.record("acme", [("person", "x")], "r1", TODAY)
    with pytest.raises(ValueError, match="delivery kind"):
        ledger.delivered("acme", "companies", "x")
    with pytest.raises(ValueError, match="client name is empty"):
        ledger.record("", [("company", "x")], "r1", TODAY)


def test_client_objects_work_as_the_client_argument(ledger):
    client = Client("acme")
    ledger.record(client, [("company", "acme.com")], "r1", TODAY)
    assert ledger.delivered("acme", "company", "acme.com") and ledger.delivered(client, "company", "acme.com")


def test_summary_and_clients_with_history(ledger):
    assert ledger.summary("acme") == {
        "client": "acme", "deliveries": 0, "first_delivery": None, "last_delivery": None, "items": 0,
        "by_kind": {"company": 0, "job": 0, "contact": 0}, "company": 0, "job": 0, "contact": 0,
        "suppressed": 0}
    ledger.record("acme", [("company", "a.com"), ("job", "a.com|1"), ("contact", "x@a.com")], "r1", LAST_WEEK)
    ledger.record("acme", [("company", "b.com"), ("job", "b.com|1"), ("job", "b.com|2")], "r2", TODAY)
    ledger.record("zeta", [("company", "a.com")], "", TODAY)
    ledger.suppress("beta", "x.com", "domain")
    ledger.suppress("acme", "x.com", "domain")
    s = ledger.summary("acme")
    assert s["deliveries"] == 2 and s["items"] == 6
    assert s["first_delivery"] == LAST_WEEK.isoformat() and s["last_delivery"] == TODAY.isoformat()
    assert (s["company"], s["job"], s["contact"]) == (2, 3, 1) and s["by_kind"]["job"] == 3
    assert s["suppressed"] == 1
    assert ledger.summary("zeta")["deliveries"] == 1          # no run id: counted by day
    assert ledger.list_clients_with_history() == ["acme", "zeta"]   # suppression alone is not history


def test_history_survives_reopening_the_database(tmp_path):
    db = tmp_path / "data" / "leadgen.db"
    with Store(db) as s:
        Ledger(s).record("acme", [("company", "acme.com")], "r1", TODAY)
        Ledger(s).suppress("acme", "Jane@Acme.com")
    with Store(db) as s:
        again = Ledger(s)
        assert again.delivered("acme", "company", "acme.com")
        assert again.is_suppressed("acme", email="jane@acme.com")


# --- client suppression -------------------------------------------------------------------------------

def test_suppress_normalises_and_is_per_client(ledger):
    assert ledger.suppress("acme", "Jane.Doe@Acme.com", "email", reason="asked") == "jane.doe@acme.com"
    assert ledger.is_suppressed("acme", email="jane.doe@ACME.com")
    assert not ledger.is_suppressed("beta", email="jane.doe@acme.com")
    assert not ledger.is_suppressed("acme", email="other@acme.com")
    assert not ledger.is_suppressed("acme")


def test_suppressed_domain_covers_emails_and_subdomains(ledger):
    ledger.suppress("acme", "https://www.BigClient.com/about", "domain")
    assert ledger.is_suppressed("acme", domain="bigclient.com")
    assert ledger.is_suppressed("acme", domain="eu.bigclient.com")
    assert ledger.is_suppressed("acme", email="someone@jobs.bigclient.com")
    assert not ledger.is_suppressed("acme", domain="notbigclient.com")
    assert ledger.suppression_match("acme", email="a@bigclient.com") == "domain"


def test_suppressed_company_names_and_linkedin(ledger):
    ledger.suppress("acme", "The Existing Client Group, Inc.", "company")
    ledger.suppress("acme", "https://www.linkedin.com/in/Jane-Doe/?trk=abc", "linkedin")
    assert ledger.is_suppressed("acme", company="Existing Client")
    assert ledger.is_suppressed("acme", company="existing client LLC")
    assert not ledger.is_suppressed("acme", company="Existing Clients Of Mine")
    assert ledger.is_suppressed("acme", linkedin="http://linkedin.com/in/jane-doe")
    assert ledger.suppression_match("acme", company="Existing Client") == "company"


def test_suppress_guesses_the_kind_and_explains_bad_values(ledger):
    ledger.suppress("acme", "jane@acme.com")
    ledger.suppress("acme", "acme.org")
    ledger.suppress("acme", "Acme Holdings")
    ledger.suppress("acme", "linkedin.com/in/bob")
    kinds = sorted((r["kind"], r["value"]) for r in ledger.list_suppressed("acme"))
    assert kinds == [("company", "acme"), ("domain", "acme.org"), ("email", "jane@acme.com"),
                     ("linkedin", "linkedin.com/in/bob")]
    with pytest.raises(ValueError, match="kind must be one of email, domain, company, linkedin"):
        ledger.suppress("acme", "x", "phone")
    with pytest.raises(ValueError, match="the company value is empty"):
        ledger.suppress("acme", "  Inc. ", "company")
    with pytest.raises(ValueError, match="not an email address"):
        ledger.suppress("acme", "acme.com", "email")
    with pytest.raises(ValueError, match="doesn't look like a website domain"):
        ledger.suppress("acme", "Acme Inc", "domain")


def test_unsuppress_and_list(ledger):
    ledger.suppress("acme", "jane@acme.com", "email", reason="asked us")
    ledger.suppress("acme", "acme.org", "domain")
    ledger.suppress("beta", "jane@acme.com", "email")
    rows = ledger.list_suppressed("acme")
    assert {r["value"] for r in rows} == {"jane@acme.com", "acme.org"}
    assert set(rows[0]) == {"client", "kind", "value", "reason", "added_at"}
    assert next(r for r in rows if r["kind"] == "email")["reason"] == "asked us"
    assert len(ledger.list_suppressed()) == 3
    assert ledger.unsuppress("acme", "JANE@acme.com") == 1          # kind worked out from the value
    assert ledger.unsuppress("acme", "acme.org", "domain") == 1
    assert ledger.unsuppress("acme", "nothing@here.com") == 0
    assert ledger.list_suppressed("acme") == []
    assert ledger.is_suppressed("beta", email="jane@acme.com")      # other clients untouched
    with pytest.raises(ValueError, match="kind must be one of"):
        ledger.unsuppress("acme", "x", "phone")


# --- hooks (unit) ------------------------------------------------------------------------------------------

def test_hooks_drop_delivered_companies(ledger):
    ledger.record("acme", [("company", "acme.com")], "r1", LAST_WEEK)
    hooks = LedgerHooks(ledger, Client("acme"), TODAY)
    assert isinstance(hooks, PipelineHooks)
    assert hooks.filter_company(company("Acme Corp", "acme.com", job())) == \
        f"already delivered to this client ({LAST_WEEK.isoformat()})"
    assert hooks.filter_company(company("Beta", "beta.com", job())) is None
    assert hooks.removed == {"company": 1, "job": 0, "contact": 0, "suppressed": 0}


def test_hooks_match_a_company_delivered_before_its_domain_was_known(ledger):
    ledger.record("acme", [("company", "name:acme")], "r1", LAST_WEEK)
    hooks = LedgerHooks(ledger, Client("acme"), TODAY)
    assert hooks.filter_company(company("Acme Corp", "acme.com", job())) is not None
    # ... but a company with a known domain never blocks a same-name company elsewhere
    ledger.record("acme", [("company", "beta.com")], "r1", LAST_WEEK)
    assert hooks.filter_company(company("Beta", "beta.co.uk", job())) is None


def test_hooks_strip_delivered_jobs_and_keep_new_ones(ledger):
    old1, old2, new = job(ext="1"), job("Controller", ext="2"), job("Payroll Clerk", ext="3")
    ledger.record("acme", [("job", "acme.com|1"), ("job", "acme.com|2"), ("company", "acme.com")], "r1",
                  LAST_WEEK)
    hooks = LedgerHooks(ledger, Client("acme", dedupe=["job", "contact"]), TODAY)
    c = company("Acme Corp", "acme.com", old1, old2, new)
    signals_list = c.signals
    assert hooks.filter_company(c) is None
    assert c.signals == [new] and c.signals is signals_list     # stripped in place
    assert hooks.removed["job"] == 2 and hooks.removed["company"] == 0


def test_hooks_drop_a_company_whose_jobs_were_all_delivered(ledger):
    ledger.record("acme", [("job", "acme.com|1")], "r1", LAST_WEEK)
    hooks = LedgerHooks(ledger, Client("acme", dedupe=["job"]), TODAY)
    assert hooks.filter_company(company("Acme Corp", "acme.com", job(ext="1"))) == \
        "all its jobs were already delivered"
    # a leftover secondary signal (funding) does not make it a new hiring lead
    funding = job("Raised Series B", type_="funding")
    assert hooks.filter_company(company("Acme Corp", "acme.com", job(ext="1"), funding)) == \
        "all its jobs were already delivered"
    # a company that only ever had a secondary signal is not affected by job de-duplication
    assert hooks.filter_company(company("Beta", "beta.com", funding)) is None
    assert hooks.removed == {"company": 0, "job": 2, "contact": 0, "suppressed": 0}


def test_hooks_respect_the_dedupe_setting(ledger):
    ledger.record("acme", [("company", "acme.com"), ("job", "acme.com|1"), ("contact", "jane@acme.com")],
                  "r1", LAST_WEEK)
    hooks = LedgerHooks(ledger, Client("acme", dedupe=[]), TODAY)
    c = company("Acme Corp", "acme.com", job(ext="1"))
    assert hooks.filter_company(c) is None and len(c.signals) == 1
    assert hooks.filter_contact(c, Contact(email="jane@acme.com")) is None
    assert hooks.removed == {"company": 0, "job": 0, "contact": 0, "suppressed": 0}


def test_hooks_redelivery_window(ledger):
    ledger.record("acme", [("company", "acme.com"), ("contact", "jane@acme.com")], "r1", date(2026, 9, 1))
    c = company("Acme Corp", "acme.com", job(ext="9"))
    assert LedgerHooks(ledger, Client("acme", redelivery_days=30), TODAY).filter_company(c) is not None
    assert LedgerHooks(ledger, Client("acme", redelivery_days=14), TODAY).filter_company(c) is None
    hooks = LedgerHooks(ledger, Client("acme", redelivery_days=14), TODAY)
    assert hooks.filter_contact(c, Contact(email="jane@acme.com")) is None


def test_hooks_contacts_delivered_under_any_identity(ledger):
    c = company()
    ledger.record("acme", [("contact", k) for k in contact_item_keys(
        Contact(first_name="Jane", last_name="Doe", email="jane@acme.com",
                linkedin_url="linkedin.com/in/jane"), c)], "r1", LAST_WEEK)
    hooks = LedgerHooks(ledger, Client("acme"), TODAY)
    # before verification: no email yet, found by LinkedIn / by name at the company
    assert hooks.filter_contact(c, Contact(full_name="Jane Doe", linkedin_url="https://linkedin.com/in/jane/"))
    assert hooks.filter_contact(c, Contact(first_name="Jane", last_name="Doe")) == \
        "contact already delivered to this client"
    # the same name at another company is another person
    assert hooks.filter_contact(company("Beta", "beta.com"), Contact(first_name="Jane", last_name="Doe")) is None
    assert hooks.filter_contact(c, Contact(first_name="Bob", last_name="Roe", email="bob@acme.com")) is None
    assert hooks.removed["contact"] == 2


def test_hooks_client_suppression(ledger):
    ledger.suppress("acme", "bigclient.com", "domain")
    ledger.suppress("acme", "Old Friend Ltd", "company")
    ledger.suppress("acme", "jane@acme.com", "email")
    ledger.suppress("acme", "linkedin.com/in/bob", "linkedin")
    client = Client("acme", exclusions={"companies": ["Their Existing Client Inc", "partner.io"],
                                        "domains": ["excluded.com"]})
    hooks = LedgerHooks(ledger, client, TODAY)
    assert hooks.filter_company(company("Big Client", "eu.bigclient.com", job())) == \
        "on this client's do-not-list (domain)"
    assert hooks.filter_company(company("Old Friend", "", job())) == "on this client's do-not-list (company name)"
    assert hooks.filter_company(company("Their Existing Client", "tec.com", job())) == \
        "on this client's do-not-list (company name)"
    assert hooks.filter_company(company("Partner", "partner.io", job())) == "on this client's do-not-list (domain)"
    assert hooks.filter_company(company("Fine Co", "fine.com", job())) is None
    c = company()
    assert hooks.filter_contact(c, Contact(email="jane@acme.com")) == "on this client's do-not-list (contact)"
    assert hooks.filter_contact(c, Contact(full_name="Bob Roe", linkedin_url="https://www.linkedin.com/in/Bob"))
    assert hooks.filter_contact(c, Contact(email="x@excluded.com"))       # the client file's domains
    assert hooks.filter_contact(c, Contact(email="x@bigclient.com"))      # company without a domain
    assert hooks.filter_contact(c, Contact(email="ok@acme.com")) is None
    assert hooks.removed == {"company": 0, "job": 0, "contact": 0, "suppressed": 8}
    # the stored list is per client
    assert LedgerHooks(ledger, Client("beta"), TODAY).filter_contact(c, Contact(email="jane@acme.com")) is None


def test_hooks_accept_a_plain_client_name(ledger):
    ledger.record("acme", [("company", "acme.com")], "r1", LAST_WEEK)
    hooks = LedgerHooks(ledger, "acme", TODAY)
    assert hooks.dedupe == set(DELIVERY_KINDS) and hooks.window is None
    assert hooks.filter_company(company()) is not None


# --- a company seen with and without its domain --------------------------------------------------------------

def _deliver(ledger, lead: Lead, day=LAST_WEEK) -> int:
    return ledger.record("acme", lead_items(lead), "r1", day)


def test_company_delivered_with_its_domain_is_recognised_without_it(ledger):
    """Week 1 the company came with its domain (Greenhouse / CSV / a merge); week 2 it
    comes name-only (Adzuna has no website): the same company, job and person."""
    jane = Contact(first_name="Jane", last_name="Doe", email="jane.doe@examplecorp.com")
    assert _deliver(ledger, Lead(company=company("Example Corp", "examplecorp.com", job(ext="adz-1")),
                                 contact=jane)) == 4    # company, job, 2 contact identities (not the name rows)
    assert ledger.summary("acme")["items"] == 4
    hooks = LedgerHooks(ledger, Client("acme"), TODAY)
    again = company("Example Corp", "", job("Financial Controller", ext="adz-99"))
    assert hooks.filter_company(again) == f"already delivered to this client ({LAST_WEEK.isoformat()})"
    # the person without an email yet: recognised by name at the (name-only) company
    assert hooks.filter_contact(again, Contact(first_name="Jane", last_name="Doe")) == \
        "contact already delivered to this client"
    # only new jobs at known companies: the ad delivered before is stripped, the new one kept
    jobs_only = LedgerHooks(ledger, Client("acme", dedupe=["job", "contact"]), TODAY)
    c = company("Example Corp", "", job(ext="adz-1"), job("Financial Controller", ext="adz-99"))
    assert jobs_only.filter_company(c) is None and [s.external_id for s in c.signals] == ["adz-99"]
    assert jobs_only.removed["job"] == 1
    # same name, different domain: a different company (as when sources are merged)
    assert hooks.filter_company(company("Example Corp", "examplecorp.co.uk", job(ext="x"))) is None
    assert hooks.filter_contact(company("Example Corp", "examplecorp.co.uk"),
                                Contact(first_name="Jane", last_name="Doe")) is None
    # other clients are not affected
    assert LedgerHooks(ledger, Client("beta"), TODAY).filter_company(again) is None


def test_company_delivered_without_its_domain_is_recognised_with_it(ledger):
    jane = Contact(first_name="Jane", last_name="Doe")
    _deliver(ledger, Lead(company=company("Example Corp", "", job(ext="adz-1")), contact=jane))
    hooks = LedgerHooks(ledger, Client("acme", dedupe=["job", "contact"]), TODAY)
    merged = company("Example Corp", "examplecorp.com", job(ext="adz-1"))
    assert hooks.filter_company(merged) == "all its jobs were already delivered"
    assert hooks.filter_contact(merged, Contact(first_name="Jane", last_name="Doe")) == \
        "contact already delivered to this client"
    assert LedgerHooks(ledger, Client("acme"), TODAY).filter_company(
        company("Example Corp", "examplecorp.com", job(ext="new"))) is not None


def test_redelivery_window_applies_to_the_name_keys_too(ledger):
    _deliver(ledger, Lead(company=company("Example Corp", "examplecorp.com", job(ext="1"))), date(2026, 9, 1))
    again = company("Example Corp", "", job(ext="2"))
    assert LedgerHooks(ledger, Client("acme", redelivery_days=30), TODAY).filter_company(again) is not None
    assert LedgerHooks(ledger, Client("acme", redelivery_days=14), TODAY).filter_company(again) is None


# --- do-not-list: what is only known later, LinkedIn pages and spellings -----------------------------------

def test_company_without_domain_found_on_the_do_not_list_through_its_people(ledger):
    """The client suppressed bigclient.com; the company arrives name-only (Adzuna), so only
    enrichment shows it is Big Client: the whole company is left out, not just the person."""
    ledger.suppress("acme", "bigclient.com", "domain")
    hooks = LedgerHooks(ledger, Client("acme"), TODAY)
    big = company("Big Client Inc", "", job(ext="adz-7"))
    assert hooks.filter_company(big) is None                       # nothing known yet
    assert hooks.filter_contact(big, Contact(first_name="Bo", last_name="Big", email="bo@bigclient.com")) == \
        "on this client's do-not-list (contact)"
    assert big.signals == []                                        # nothing left to deliver
    assert big.data["client_do_not_list"] == {"acme": "on this client's do-not-list (domain)"}
    assert hooks.company_do_not_list(big) == "on this client's do-not-list (domain)"
    # the other people there are skipped too (no verification credits spent on them)
    assert hooks.filter_contact(big, Contact(first_name="Al", last_name="Other")) == \
        "on this client's do-not-list (domain)"
    assert hooks.removed == {"company": 0, "job": 0, "contact": 0, "suppressed": 2}   # Bo + the company
    # the mark is per client
    assert LedgerHooks(ledger, Client("beta"), TODAY).company_do_not_list(big) is None
    # a company WITH its own domain is not blamed for one person's address elsewhere
    fine = company("Fine Co", "fine.com", job(ext="1"))
    assert hooks.filter_contact(fine, Contact(email="moved@bigclient.com")) is not None
    assert fine.signals and "client_do_not_list" not in fine.data


def test_do_not_list_uses_the_email_domain_hint_and_people_found(ledger):
    ledger.suppress("acme", "bigclient.com", "domain")
    client = Client("acme", exclusions={"domains": ["excluded.com"]})
    hooks = LedgerHooks(ledger, client, TODAY)
    # a source / Hunter left the company's email domain: known before any paid lookup
    hinted = company("Big Client Inc", "", job())
    hinted.data["email_domain"] = "bigclient.com"
    assert hooks.filter_company(hinted) == "on this client's do-not-list (domain)"
    # after enrichment: the people found show the domain (the client file's exclusions too)
    late = company("Excluded Holdings", "", job())
    late.contacts = [Contact(first_name="Ed", last_name="X", email="ed@excluded.com")]
    assert hooks.company_do_not_list(late) == "on this client's do-not-list (domain)"
    assert hooks.company_do_not_list(company("Fine Co", "", job())) is None


def test_company_linkedin_page_on_the_client_do_not_list(ledger):
    ledger.suppress("acme", "https://www.linkedin.com/company/example-corp/", "linkedin")
    hooks = LedgerHooks(ledger, Client("acme"), TODAY)
    for url in ("https://www.linkedin.com/company/example-corp", "https://uk.linkedin.com/company/Example-Corp/about/"):
        c = company("Example Corp", "examplecorp.com", job())
        c.linkedin_url = url
        assert hooks.filter_company(c) == "on this client's do-not-list (LinkedIn)"
    other = company("Other Corp", "other.com", job())
    other.linkedin_url = "https://www.linkedin.com/company/other-corp"
    assert hooks.filter_company(other) is None
    assert hooks.removed["suppressed"] == 2


def test_linkedin_key_ignores_country_hosts_and_extra_path():
    same = ["https://uk.linkedin.com/in/jane-doe-123", "https://www.linkedin.com/in/Jane-Doe-123/",
            "http://linkedin.com/in/jane-doe-123?trk=x", "https://de.linkedin.com/in/jane-doe-123/en",
            "https://m.linkedin.com/in/jane-doe-123/details/experience/", "www.uk.linkedin.com/in/jane-doe-123"]
    assert {linkedin_key(u) for u in same} == {"linkedin.com/in/jane-doe-123"}
    assert linkedin_key("https://ca.linkedin.com/company/acme/jobs/") == "linkedin.com/company/acme"
    assert linkedin_key("https://notlinkedin.com/in/jane") == "notlinkedin.com/in/jane"
    assert linkedin_key("") == ""


def test_linkedin_do_not_list_and_dedupe_across_country_hosts(ledger, store):
    ledger.suppress("acme", "https://uk.linkedin.com/in/jane-doe-123", "linkedin")
    assert [r["value"] for r in ledger.list_suppressed("acme")] == ["linkedin.com/in/jane-doe-123"]
    # an entry stored in the old spelling still matches
    store.conn.execute("INSERT INTO client_suppression (client, kind, value, reason, added_at) "
                       "VALUES ('acme', 'linkedin', 'fr.linkedin.com/in/bob-roe', '', '2026-01-01')")
    hooks = LedgerHooks(ledger, Client("acme"), TODAY)
    c = company()
    assert hooks.filter_contact(c, Contact(full_name="Jane Doe",
                                           linkedin_url="https://www.linkedin.com/in/jane-doe-123"))
    assert hooks.filter_contact(c, Contact(full_name="Bob Roe", linkedin_url="https://linkedin.com/in/bob-roe/"))
    assert ledger.is_suppressed("acme", linkedin="https://www.linkedin.com/in/bob-roe")
    assert ledger.unsuppress("acme", "https://www.linkedin.com/in/bob-roe") == 1
    assert ledger.unsuppress("acme", "https://de.linkedin.com/in/jane-doe-123/", "linkedin") == 1
    assert ledger.list_suppressed("acme") == []
    # a person delivered with one spelling is recognised with another
    ledger.record("acme", lead_items(Lead(company=c, contact=Contact(
        full_name="Kim Lee", linkedin_url="https://uk.linkedin.com/in/kim-lee"))), "r1", LAST_WEEK)
    assert LedgerHooks(ledger, Client("acme", dedupe=["contact"]), TODAY).filter_contact(
        company("Beta", "beta.com"), Contact(full_name="Kim Lee", linkedin_url="https://www.linkedin.com/in/kim-lee/"))


# --- hooks through a real delivery-mode pipeline run -----------------------------------------------------------

class SpyFinder(ContactFinder):
    """Offline finder that records every company enrichment was asked about."""

    name = "ledger_spy"
    offline = True
    seen: List[str] = []

    def find(self, company: Company) -> List[Contact]:
        SpyFinder.seen.append(company.name)
        d = company.domain
        return [
            Contact(first_name="Casey", last_name="Money", title="CFO", email=f"casey@{d}",
                    email_status="valid", source=self.name,
                    linkedin_url=f"https://www.linkedin.com/in/casey-{d.split('.')[0]}"),
            Contact(first_name="Robin", last_name="Hire", title="Head of Talent", email=f"robin@{d}",
                    email_status="valid", source=self.name),
        ]


registry.register("finder", "ledger_spy", "tests.test_ledger:SpyFinder")

HEADER = ["company", "domain", "location", "industry", "employees", "signal_type", "signal_title",
          "signal_date", "signal_url", "signal_id"]
WEEK1 = [
    ["Acme Corp", "acme-ledger-demo.com", "Austin, TX", "Software", 120, "job_posting", "Senior Accountant",
     "2026-09-22", "https://acme-ledger-demo.com/jobs/1?utm_source=board", "a-1"],
    ["Acme Corp", "acme-ledger-demo.com", "Austin, TX", "Software", 120, "job_posting", "Financial Controller",
     "2026-09-20", "https://acme-ledger-demo.com/jobs/2", "a-2"],
    ["Beta LLC", "beta-ledger-demo.com", "Tulsa, OK", "Logistics", 300, "job_posting", "Staff Accountant",
     "2026-09-21", "", ""],
    ["Gamma Inc", "gamma-ledger-demo.com", "Dallas, TX", "Software", 80, "job_posting", "Accountant",
     "2026-09-23", "https://gamma-ledger-demo.com/careers/acct", ""],
]
BASE = """\
name: ledger-base
sources:
  - type: csv
    path: jobs.csv
enrichment:
  finders: [{type: ledger_spy}]
  verifier: {type: basic}
scoring:
  tiers: {hot: 40, normal: 10}
notify:
  channels: []
"""


def _write_jobs(folder: Path, rows: List[list]) -> None:
    with open(folder / "jobs.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)


@pytest.fixture
def delivery_ws(tmp_path):
    folder = tmp_path / "base"
    folder.mkdir()
    (folder / "base.yaml").write_text(BASE, encoding="utf-8")
    _write_jobs(folder, WEEK1)
    SpyFinder.seen = []
    return folder


def _client(folder: Path, **kw) -> Client:
    return Client("acme", playbook=str(folder / "base.yaml"), roles=["accountant", "controller"],
                  buyer_titles=["CFO", "Head of Talent"], freshness_days=30, leads_per_week=10, **kw)


def _run(client: Client, store: Store, out: Path, dry_run: bool = False):
    pb = client_playbook(client, env={})
    ctx = Context(playbook=pb, http=FakeHttp(), store=store, env={}, today=TODAY,
                  log=logging.getLogger("leadgen.test"), dry_run=dry_run)
    hooks = LedgerHooks(Ledger(store), client, TODAY)
    SpyFinder.seen = []
    res = Pipeline(ctx, out_dir=out, hooks=hooks).run()
    return res, hooks


def _record(ledger: Ledger, client: Client, res) -> int:
    return sum(ledger.record(client.name, lead_items(ld), res.run_id, TODAY) for ld in res.leads)


def test_delivered_items_never_reach_enrichment_or_results_again(delivery_ws, store, tmp_path):
    client = _client(delivery_ws)
    ledger = Ledger(store)
    out = tmp_path / "out"

    # week 1: everything is new
    res1, hooks1 = _run(client, store, out)
    assert res1.mode == "delivery" and not res1.errors
    assert sorted(ld.company.name for ld in res1.leads) == ["Acme Corp", "Beta LLC", "Gamma Inc"]
    assert sorted(SpyFinder.seen) == ["Acme Corp", "Beta LLC", "Gamma Inc"]
    assert all(ld.contact and ld.contact.first_name == "Casey" for ld in res1.leads)
    assert hooks1.removed == {"company": 0, "job": 0, "contact": 0, "suppressed": 0}
    assert _record(ledger, client, res1) > 0

    # week 2, same data: every company was delivered -> dropped before any lookup
    res2, hooks2 = _run(client, store, out)
    assert res2.leads == [] and SpyFinder.seen == []
    assert res2.counts["hook_rejected"] == 3
    assert hooks2.removed == {"company": 3, "job": 0, "contact": 0, "suppressed": 0}
    delivery_rejects = [r for r in res2.rejected if r["stage"] == "delivery"]
    assert {r["company"] for r in delivery_rejects} == {"Acme Corp", "Beta LLC", "Gamma Inc"}
    assert all(r["reason"] == f"already delivered to this client ({TODAY.isoformat()})"
               for r in delivery_rejects)

    # week 3: new jobs at known companies are allowed (no 'company' dedupe); Acme posts a
    # new role, Gamma's old ad comes back with tracking junk in its URL
    _write_jobs(delivery_ws, WEEK1[:3] + [
        ["Gamma Inc", "gamma-ledger-demo.com", "Dallas, TX", "Software", 80, "job_posting", "Accountant",
         "2026-09-23", "https://www.gamma-ledger-demo.com/careers/acct/?utm_campaign=wk3#apply", ""],
        ["Acme Corp", "acme-ledger-demo.com", "Austin, TX", "Software", 120, "job_posting",
         "Payroll Accountant", "2026-09-23", "https://acme-ledger-demo.com/jobs/3", "a-3"],
    ])
    client3 = _client(delivery_ws, dedupe=["job", "contact"])
    res3, hooks3 = _run(client3, store, out)
    assert SpyFinder.seen == ["Acme Corp"]                   # only the company with a new job was enriched
    assert [ld.company.name for ld in res3.leads] == ["Acme Corp"]
    lead = res3.leads[0]
    assert [s.external_id for s in lead.company.signals] == ["a-3"]   # only the new job is delivered
    assert lead.contact is not None and lead.contact.first_name == "Robin"   # Casey was delivered already
    assert hooks3.removed == {"company": 0, "job": 4, "contact": 1, "suppressed": 0}
    reasons = {r["company"]: r["reason"] for r in res3.rejected if r["stage"] == "delivery"}
    assert reasons == {"Beta LLC": "all its jobs were already delivered",
                       "Gamma Inc": "all its jobs were already delivered"}


def test_redelivery_window_through_the_pipeline(delivery_ws, store, tmp_path):
    client = _client(delivery_ws, redelivery_days=7)
    ledger = Ledger(store)
    ledger.record("acme", [("company", "acme-ledger-demo.com"), ("company", "beta-ledger-demo.com")], "old",
                  today=TODAY - timedelta(days=7))      # a week ago: allowed again
    ledger.record("acme", [("company", "gamma-ledger-demo.com")], "old", today=TODAY - timedelta(days=2))
    res, hooks = _run(client, store, tmp_path / "out")
    assert sorted(ld.company.name for ld in res.leads) == ["Acme Corp", "Beta LLC"]
    assert hooks.removed["company"] == 1 and "Gamma Inc" not in SpyFinder.seen


@pytest.mark.parametrize("dry_run", [False, True])
def test_client_suppression_through_the_pipeline(delivery_ws, store, tmp_path, dry_run):
    ledger = Ledger(store)
    ledger.suppress("acme", "beta-ledger-demo.com", "domain", reason="their client")
    ledger.suppress("acme", "casey@acme-ledger-demo.com", "email")
    client = _client(delivery_ws, exclusions={"companies": ["Gamma"]})
    res, hooks = _run(client, store, tmp_path / "out", dry_run=dry_run)
    assert SpyFinder.seen == ["Acme Corp"]
    assert [ld.company.name for ld in res.leads] == ["Acme Corp"]
    assert res.leads[0].contact.first_name == "Robin"
    assert hooks.removed == {"company": 0, "job": 0, "contact": 0, "suppressed": 3}
    reasons = {r["company"]: r["reason"] for r in res.rejected if r["stage"] == "delivery"}
    assert reasons == {"Beta LLC": "on this client's do-not-list (domain)",
                       "Gamma Inc": "on this client's do-not-list (company name)"}
    # hooks only read the ledger: nothing was recorded by the run itself
    assert ledger.summary("acme")["items"] == 0


def test_company_seen_again_without_its_domain_never_reaches_enrichment(delivery_ws, store, tmp_path):
    client = _client(delivery_ws)
    ledger = Ledger(store)
    res1, _ = _run(client, store, tmp_path / "out")
    assert _record(ledger, client, res1) > 0
    # next week Acme comes from a source without websites (Adzuna), with a new job
    _write_jobs(delivery_ws, [["Acme Corp", "", "Austin, TX", "Software", 120, "job_posting",
                               "Payroll Accountant", "2026-09-23", "", "adz-9"]])
    res2, hooks2 = _run(client, store, tmp_path / "out")
    assert res2.leads == [] and SpyFinder.seen == []
    assert hooks2.removed["company"] == 1
    assert [r["reason"] for r in res2.rejected if r["stage"] == "delivery"] == [
        f"already delivered to this client ({TODAY.isoformat()})"]


class PeopleFinder(ContactFinder):
    """Offline finder: the people listed for a company name (``PEOPLE``)."""

    name = "ledger_people"
    offline = True
    PEOPLE: dict = {}

    def find(self, company: Company) -> List[Contact]:
        return [Contact(**p) for p in PeopleFinder.PEOPLE.get(company.name, [])]


registry.register("finder", "ledger_people", "tests.test_ledger:PeopleFinder")


@pytest.mark.parametrize("stored", [
    True,
    # the client file's exclusions.domains are icp.exclude_domains: select_contacts drops Bo before
    # filter_contact sees him, so the hooks never learn the domain. deliver() must also drop leads for
    # which LedgerHooks.company_do_not_list(lead.company) gives a reason (leadgen/delivery/run.py).
    pytest.param(False, marks=pytest.mark.xfail(reason="needs deliver()/select_leads to call "
                                                       "LedgerHooks.company_do_not_list after enrichment")),
])
def test_do_not_list_company_found_only_by_enrichment_is_not_delivered(delivery_ws, store, tmp_path, stored):
    """A name-only company whose decision-maker turns out to be at a do-not-list domain is
    left out of the client file (before: delivered with 'no decision-maker found')."""
    from leadgen.delivery.run import deliver

    (delivery_ws / "base.yaml").write_text(BASE.replace("ledger_spy", "ledger_people"), encoding="utf-8")
    _write_jobs(delivery_ws, [
        ["Big Client Inc", "", "Austin, TX", "Software", 120, "job_posting", "Staff Accountant", "2026-09-23", "",
         "adz-7"],
        ["Fine Co", "fine-ledger-demo.com", "Austin, TX", "Software", 120, "job_posting", "Accountant",
         "2026-09-23", "", "f-1"],
    ])
    PeopleFinder.PEOPLE = {
        "Big Client Inc": [dict(first_name="Bo", last_name="Big", title="CFO", email="bo@bigclient-demo.com",
                                email_status="valid")],
        "Fine Co": [dict(first_name="Fay", last_name="Fine", title="CFO", email="fay@fine-ledger-demo.com",
                         email_status="valid")],
    }
    ledger = Ledger(store)
    if stored:
        ledger.suppress("acme", "bigclient-demo.com", "domain")
        client = _client(delivery_ws)
    else:
        client = _client(delivery_ws, exclusions={"domains": ["bigclient-demo.com"]})
    res = deliver(client, store=store, env={}, http=FakeHttp(), today=TODAY, out_dir=tmp_path / "files")
    assert [r["company"] for r in res.package.rows] == ["Fine Co"]
    items = ledger.summary("acme")
    assert items["company"] == 1
