"""Tests for leadgen.contacts: title ranking, generic mailboxes, selection and the finder waterfall."""
from __future__ import annotations

import logging
from typing import Any, Dict, List

import pytest

from leadgen import registry
from leadgen.context import MissingCredentialError
from leadgen.contacts import (ContactWaterfall, canonical_title, is_excluded_title, is_generic_email,
                              same_person, select_contacts, title_matches, title_rank)
from leadgen.enrich.base import ContactFinder
from leadgen.http import HttpError
from leadgen.models import Company, Contact, EmailStatus

BUYERS = {"titles": ["CFO", "VP Finance", "Finance Director", "Controller"]}


# --- fake finders (module level so the registry can import them) -------------------------

class FakeFinder(ContactFinder):
    """Configurable finder.

    config: ``people`` (list of Contact kwargs returned by find), ``emails``
    ({full_name: email} used by complete), ``status`` (email_status for completed
    emails), ``raise`` (error to raise from find: cred | http401 | http500 | boom),
    ``complete_in_place`` (mutate instead of returning a new Contact).
    """

    name = "fake_x"
    offline = False

    def __init__(self, config: Dict[str, Any], ctx: Any):
        super().__init__(config, ctx)
        self.calls: List[tuple] = []

    def find(self, company: Company) -> List[Contact]:
        self.calls.append(("find", company.name))
        err = self.config.get("raise")
        if err == "cred":
            self.secret()  # no key configured -> MissingCredentialError
        if err == "http401":
            raise HttpError(401, "https://api.fake.test/people?api_key=SECRET", '{"error":"bad key"}')
        if err == "http500":
            raise HttpError(500, "https://api.fake.test/people", "boom")
        if err == "boom":
            raise ValueError("unexpected payload")
        return [Contact(source=self.name, **p) for p in self.config.get("people", [])]

    def complete(self, company: Company, contact: Contact) -> Contact:
        self.calls.append(("complete", contact.full_name))
        email = (self.config.get("emails") or {}).get(contact.full_name)
        if not email:
            return contact
        status = self.config.get("status", EmailStatus.UNKNOWN)
        if self.config.get("complete_in_place"):
            contact.email, contact.email_status = email, status
            return contact
        return Contact(full_name=contact.full_name, email=email, email_status=status, source=self.name)


class OfflineFakeFinder(FakeFinder):
    name = "fake_offline"
    offline = True


class BrokenInitFinder(ContactFinder):
    name = "fake_broken"

    def __init__(self, config: Dict[str, Any], ctx: Any):
        raise RuntimeError("cannot build")


registry.register("finder", "fake_x", "tests.test_contacts:FakeFinder")
registry.register("finder", "fake_offline", "tests.test_contacts:OfflineFakeFinder")
registry.register("finder", "fake_broken", "tests.test_contacts:BrokenInitFinder")


def person(name: str, title: str, email: str = "", **kw) -> Dict[str, Any]:
    return {"full_name": name, "title": title, "email": email, **kw}


# --- titles ---------------------------------------------------------------------------------

@pytest.mark.parametrize("title,rank", [
    ("CFO", 0),
    ("Chief Financial Officer", 0),
    ("Chief Financial Officer (CFO)", 0),
    ("CFO & COO", 0),
    ("VP Finance", 1),
    ("VP of Finance", 1),
    ("Vice President, Finance", 1),
    ("Vice-President Finance & Operations", 1),
    ("SVP, Finance", 1),
    ("EVP Finance", 1),
    ("Finance Director", 2),
    ("Director of Finance", 2),
    ("Financial Controller", 3),
    ("Group Financial Controllers", 3),
    ("Senior Accountant", None),
    ("Chief of Staff to the CEO", None),
    ("Director", None),
    ("", None),
    (None, None),
])
def test_title_rank(make_ctx, title, rank):
    ctx = make_ctx(buyers=BUYERS)
    assert title_rank(title, ctx) == rank


def test_title_rank_first_matching_entry_wins(make_ctx):
    ctx = make_ctx(buyers={"titles": ["Head of Finance", "Head"]})
    assert title_rank("Finance Head", ctx) == 0
    assert title_rank("Head of People", ctx) == 1


def test_title_rank_without_buyer_titles(make_ctx):
    ctx = make_ctx()
    assert title_rank("Anything At All", ctx) == 0
    assert title_rank("Marketing Intern", ctx) is None
    assert title_rank("", ctx) is None


def test_excluded_titles_never_rank(make_ctx):
    ctx = make_ctx(buyers={"titles": ["Finance"], "exclude_titles": ["intern", "assistant"]})
    assert is_excluded_title("Finance Intern", ctx)
    assert is_excluded_title("Interns - Finance", ctx)
    assert is_excluded_title("Asst. Finance Manager", ctx)
    assert not is_excluded_title("Internal Audit Finance Lead", ctx)
    assert not is_excluded_title("", ctx)
    assert title_rank("Finance Intern", ctx) is None
    assert title_rank("Internal Finance Lead", ctx) == 0


@pytest.mark.parametrize("title,wanted,expected", [
    ("Head of HR", "HR Director", False),
    ("HR Director", "Human Resources Director", True),
    ("Director of Human Resources", "HR Director", True),
    ("Talent Acquisition Lead", "TA Lead", True),
    ("MD", "Managing Director", True),
    ("GM, EMEA", "General Manager", True),
    ("Sr. Dir. Operations", "Senior Director Ops", True),
    ("Co-Founder & CEO", "Founder", True),
    ("Cofounder", "Co-Founder", True),
    ("Vice President Sales", "President", False),
    ("Director of Sales", "CTO", False),
    ("Three Rivers Manager", "HR", False),
    ("VP Sales at Acme Corp", "VP Sales", True),
    ("Head of People reporting to the CEO", "CEO", False),
    ("Chief Technical Officer", "CTO", True),
])
def test_title_matches_abbreviations(title, wanted, expected):
    assert title_matches(title, wanted) is expected


def test_canonical_title():
    assert canonical_title("VP of Finance") == ("vp", "finance")
    assert canonical_title("Vice President, Finance") == ("vp", "finance")
    assert canonical_title("SVP Finance") == ("senior", "vp", "finance")
    assert canonical_title("") == ()


# --- generic mailboxes ------------------------------------------------------------------------

@pytest.mark.parametrize("email", [
    "info@acme.com", "hello@acme.com", "contact@acme.com", "sales@acme.com", "admin@acme.com",
    "office@acme.com", "support@acme.com", "hr@acme.com", "jobs@acme.com", "careers@acme.com",
    "recruitment@acme.com", "team@acme.com", "enquiries@acme.com", "inquiries@acme.com",
    "mail@acme.com", "help@acme.com", "billing@acme.com", "accounts@acme.com", "marketing@acme.com",
    "press@acme.com", "noreply@acme.com", "no-reply@acme.com", "do-not-reply@acme.com",
    "INFO@Acme.com", "info.uk@acme.com", "info2@acme.com", "customer.service@acme.com",
])
def test_generic_emails(email):
    assert is_generic_email(email)


@pytest.mark.parametrize("email", ["jane@acme.com", "jane.doe@acme.com", "j.smith@acme.com",
                                   "bill@acme.com", "mark@acme.com", "", None, "not-an-email"])
def test_personal_emails_are_not_generic(email):
    assert not is_generic_email(email)


# --- select_contacts -----------------------------------------------------------------------------

def test_select_ranks_by_title_then_email_quality(make_ctx):
    ctx = make_ctx(buyers=BUYERS)
    company = Company(name="Acme", contacts=[
        Contact(full_name="Sam Sales", title="Sales Manager", email="sam@acme.com", email_status="valid"),
        Contact(full_name="Vic Vp", title="VP Finance", email="vic@acme.com", email_status="risky"),
        Contact(full_name="Val Vp", title="VP of Finance", email="val@acme.com", email_status="valid"),
        Contact(full_name="Nia Noemail", title="VP Finance"),
        Contact(full_name="Ian Invalid", title="VP Finance", email="ian@acme.com", email_status="invalid"),
        Contact(full_name="Una Unknown", title="VP Finance", email="una@acme.com"),
        Contact(full_name="Cara Cfo", title="CFO"),
    ])
    names = [c.full_name for c in select_contacts(company, ctx, limit=10)]
    assert names == ["Cara Cfo", "Val Vp", "Vic Vp", "Una Unknown", "Ian Invalid", "Nia Noemail", "Sam Sales"]


def test_select_confidence_breaks_ties_and_limit(make_ctx):
    ctx = make_ctx(buyers=BUYERS)
    company = Company(name="Acme", contacts=[
        Contact(full_name="Low", title="CFO", email="low@acme.com", confidence=0.2),
        Contact(full_name="High", title="CFO", email="high@acme.com", confidence=0.9),
        Contact(full_name="Other", title="Controller", email="o@acme.com"),
    ])
    assert [c.full_name for c in select_contacts(company, ctx, limit=2)] == ["High", "Low"]
    assert select_contacts(company, ctx, limit=0) == []
    assert len(select_contacts(company, ctx, limit=None)) == 3


def test_select_drops_excluded_and_dedupes(make_ctx):
    ctx = make_ctx(buyers=BUYERS)
    company = Company(name="Acme", contacts=[
        Contact(full_name="Ann", title="Finance Intern", email="ann@acme.com"),
        Contact(full_name="Bo", title="CFO", email="bo@acme.com"),
        Contact(full_name="Bo B", title="CFO", email="BO@acme.com"),
        Contact(title="CFO"),  # no name, email or LinkedIn: nothing to act on
    ])
    out = select_contacts(company, ctx, limit=10)
    assert [c.email for c in out] == ["bo@acme.com"]


def test_select_generic_mailboxes(make_ctx):
    ctx = make_ctx(buyers=BUYERS)
    named = Contact(full_name="Jane Doe", title="CFO", email="info@acme.com", email_status="valid",
                    email_candidates=["finance@acme.com", "jane@acme.com"])
    role = Contact(title="CFO", email="accounts@acme.com")
    company = Company(name="Acme", contacts=[named, role])
    out = select_contacts(company, ctx, limit=5)
    assert len(out) == 1
    jane = out[0]
    assert jane.full_name == "Jane Doe" and jane.email == "" and jane.email_status == EmailStatus.UNKNOWN
    assert jane.email_candidates == ["jane@acme.com"]
    # the company is untouched
    assert named.email == "info@acme.com" and company.contacts == [named, role]
    assert named.email_candidates == ["finance@acme.com", "jane@acme.com"]

    allow = make_ctx(buyers={**BUYERS, "allow_generic_emails": True})
    assert [c.email for c in select_contacts(company, allow, limit=5)] == ["info@acme.com", "accounts@acme.com"]


def test_select_keeps_identity_of_untouched_contacts(make_ctx):
    ctx = make_ctx()
    c = Contact(full_name="Jane Doe", title="Owner", email="jane@acme.com")
    assert select_contacts(Company(name="Acme", contacts=[c]), ctx, limit=1)[0] is c


def test_select_empty_company(make_ctx):
    assert select_contacts(Company(name="Acme"), make_ctx(), limit=3) == []


def test_same_person():
    a = Contact(full_name="Jane Doe", title="CFO")
    assert same_person(a, Contact(full_name="jane  doe", email="jane@acme.com"))
    assert same_person(Contact(linkedin_url="https://www.linkedin.com/in/jd/"),
                       Contact(full_name="J D", linkedin_url="http://linkedin.com/in/jd"))
    assert not same_person(Contact(full_name="John Smith", email="js1@acme.com"),
                           Contact(full_name="John Smith", email="js2@acme.com"))
    assert not same_person(Contact(full_name="A"), Contact(full_name="B"))


# --- ContactWaterfall ----------------------------------------------------------------------------

def finders(*cfgs: Dict[str, Any]) -> Dict[str, Any]:
    return {"finders": list(cfgs)}


def test_waterfall_stops_at_first_reachable_buyer(make_ctx):
    ctx = make_ctx(buyers=BUYERS, enrichment=finders(
        {"type": "fake_x", "label": "first", "people": [person("Jane Doe", "CFO", "jane@acme.com")]},
        {"type": "fake_x", "label": "second", "people": [person("Other", "VP Finance", "o@acme.com")]},
    ))
    wf = ContactWaterfall(ctx)
    company = Company(name="Acme", domain="acme.com")
    assert wf.enrich(company) == ["first"]
    assert [c.email for c in company.contacts] == ["jane@acme.com"]
    assert company.contacts[0].source == "fake_x"
    assert wf.finders[1].calls == []


def test_waterfall_falls_through_and_merges(make_ctx):
    ctx = make_ctx(buyers=BUYERS, enrichment=finders(
        {"type": "fake_x", "label": "names", "people": [person("Jane Doe", "CFO"),
                                                        person("Sam", "Sales Manager", "sam@acme.com")]},
        {"type": "fake_x", "label": "emails",
         "people": [person("Jane Doe", "", "jane@acme.com", linkedin_url="https://linkedin.com/in/jane")]},
    ))
    errors: List[str] = []
    wf = ContactWaterfall(ctx, errors=errors)
    company = Company(name="Acme", domain="acme.com")
    assert wf.enrich(company) == ["names", "emails"]
    assert errors == []
    jane = next(c for c in company.contacts if c.full_name == "Jane Doe")
    assert len(company.contacts) == 2
    assert jane.title == "CFO" and jane.email == "jane@acme.com"
    assert jane.linkedin_url == "https://linkedin.com/in/jane"


def test_waterfall_completes_ranked_contacts_best_first(make_ctx):
    ctx = make_ctx(buyers=BUYERS, enrichment=finders(
        {"type": "fake_x", "label": "a",
         "people": [person("Sam", "Sales Manager"), person("Vic", "VP Finance"), person("Cara", "CFO")],
         "emails": {"Cara": "cara@acme.com", "Vic": "vic@acme.com"}, "status": "valid"},
    ))
    wf = ContactWaterfall(ctx)
    company = Company(name="Acme")
    wf.enrich(company)
    calls = wf.finders[0].calls
    assert calls == [("find", "Acme"), ("complete", "Cara")]  # stops once Cara is reachable
    cara = next(c for c in company.contacts if c.full_name == "Cara")
    assert cara.email == "cara@acme.com" and cara.email_status == "valid"
    assert next(c for c in company.contacts if c.full_name == "Vic").email == ""


def test_waterfall_complete_in_place_and_unranked_are_not_completed(make_ctx):
    ctx = make_ctx(buyers=BUYERS, enrichment=finders(
        {"type": "fake_x", "people": [person("Sam", "Sales Manager"), person("Vic", "VP Finance")],
         "emails": {"Vic": "vic@acme.com", "Sam": "sam@acme.com"}, "complete_in_place": True},
    ))
    wf = ContactWaterfall(ctx)
    company = Company(name="Acme")
    wf.enrich(company)
    assert ("complete", "Sam") not in wf.finders[0].calls
    assert next(c for c in company.contacts if c.full_name == "Vic").email == "vic@acme.com"


def test_waterfall_candidates_count_as_reachable(make_ctx):
    ctx = make_ctx(buyers=BUYERS, enrichment=finders(
        {"type": "fake_x", "label": "guess",
         "people": [person("Jane Doe", "CFO", email_candidates=["jane@acme.com", "jdoe@acme.com"])]},
        {"type": "fake_x", "label": "never"},
    ))
    wf = ContactWaterfall(ctx)
    assert wf.enrich(Company(name="Acme")) == ["guess"]


def test_waterfall_invalid_or_generic_email_is_not_enough(make_ctx):
    ctx = make_ctx(buyers=BUYERS, enrichment=finders(
        {"type": "fake_x", "label": "bad", "people": [
            person("Jane Doe", "CFO", "jane@acme.com", email_status="invalid"),
            person("Val", "Controller", "info@acme.com")]},
        {"type": "fake_x", "label": "good", "people": [person("Vic", "VP Finance", "vic@acme.com")]},
    ))
    wf = ContactWaterfall(ctx)
    assert wf.enrich(Company(name="Acme")) == ["bad", "good"]


def test_waterfall_skip_if_contact_present(make_ctx):
    cfg = {"type": "fake_x", "people": [person("New", "CFO", "new@acme.com")]}
    ctx = make_ctx(buyers=BUYERS, enrichment=finders(cfg))
    company = Company(name="Acme", contacts=[Contact(full_name="Vic", title="VP Finance", email="vic@acme.com")])
    wf = ContactWaterfall(ctx)
    assert wf.enrich(company) == [] and wf.finders[0].calls == []
    # an unranked or invalid contact does not count
    for existing in (Contact(full_name="Sam", title="Sales", email="sam@acme.com"),
                     Contact(full_name="Vic", title="VP Finance", email="vic@acme.com", email_status="invalid")):
        c2 = Company(name="Acme", contacts=[existing])
        assert wf.enrich(c2) == ["fake_x"]
    # switched off: finders run anyway
    off = make_ctx(buyers=BUYERS, enrichment={**finders(cfg), "skip_if_contact_present": False})
    assert ContactWaterfall(off).enrich(Company(name="Acme", contacts=[
        Contact(full_name="Vic", title="VP Finance", email="vic@acme.com")])) == ["fake_x"]


def test_waterfall_without_buyer_titles_takes_anyone(make_ctx):
    ctx = make_ctx(enrichment=finders(
        {"type": "fake_x", "label": "a", "people": [person("Pat", "", "pat@acme.com")]},
        {"type": "fake_x", "label": "b"},
    ))
    assert ContactWaterfall(ctx).enrich(Company(name="Acme")) == ["a"]


def test_waterfall_errors_are_collected_and_next_finder_runs(make_ctx, caplog):
    ctx = make_ctx(buyers=BUYERS, enrichment=finders(
        {"type": "fake_x", "label": "flaky", "raise": "http500"},
        {"type": "fake_x", "label": "weird", "raise": "boom"},
        {"type": "fake_x", "label": "ok", "people": [person("Jane", "CFO", "jane@acme.com")]},
    ))
    errors: List[str] = []
    wf = ContactWaterfall(ctx, errors=errors)
    with caplog.at_level(logging.ERROR, logger="leadgen.test"):
        assert wf.enrich(Company(name="Acme")) == ["flaky", "weird", "ok"]
    assert errors[0].startswith("finder flaky: HTTP 500 for https://api.fake.test/people")
    assert errors[1] == "finder weird: unexpected payload"
    assert "finder weird" in caplog.text
    # transient errors do not disable the finder
    wf.enrich(Company(name="Beta"))
    assert len(errors) == 4


def test_waterfall_disables_finder_after_credential_or_auth_error(make_ctx):
    ctx = make_ctx(buyers=BUYERS, enrichment=finders(
        {"type": "fake_x", "label": "nokey", "raise": "cred", "api_key_env": "FAKE_KEY"},
        {"type": "fake_x", "label": "badkey", "raise": "http401"},
        {"type": "fake_x", "label": "last"},
    ))
    errors: List[str] = []
    wf = ContactWaterfall(ctx, errors=errors)
    assert wf.enrich(Company(name="A")) == ["nokey", "badkey", "last"]
    assert errors[0] == "finder nokey: fake_x: missing credential (set $FAKE_KEY)"
    assert errors[1].startswith("finder badkey: HTTP 401") and "SECRET" not in errors[1]
    assert wf.enrich(Company(name="B")) == ["last"]
    assert len(errors) == 2


def test_waterfall_construction_problems(make_ctx):
    ctx = make_ctx(enrichment=finders(
        {"type": "does_not_exist"},
        {"type": "fake_broken", "label": "broken"},
        {"type": "fake_x", "enabled": False},
        {"type": "fake_offline", "label": "ok"},
    ))
    errors: List[str] = []
    wf = ContactWaterfall(ctx, errors=errors)
    assert wf.names == ["ok"]
    assert errors[0].startswith("finder does_not_exist: unknown finder type 'does_not_exist'")
    assert errors[1] == "finder broken: cannot build"


def test_waterfall_dry_run_only_offline_finders(make_ctx):
    ctx = make_ctx(dry_run=True, buyers=BUYERS, enrichment=finders(
        {"type": "fake_x", "label": "paid", "people": [person("Paid", "CFO", "p@acme.com")]},
        {"type": "fake_offline", "label": "local", "people": [person("Local", "CFO", "l@acme.com")]},
    ))
    wf = ContactWaterfall(ctx)
    assert wf.names == ["local"]
    company = Company(name="Acme")
    assert wf.enrich(company) == ["local"]
    assert [c.full_name for c in company.contacts] == ["Local"]


def test_waterfall_no_finders(make_ctx):
    wf = ContactWaterfall(make_ctx())
    assert wf.enrich(Company(name="Acme")) == [] and wf.errors == []


def test_waterfall_ignores_non_contacts_and_accepts_dicts(make_ctx):
    class Weird(FakeFinder):
        def find(self, company):
            return [{"full_name": "Dict Person", "title": "CFO", "email": "d@acme.com"}, "junk", None]

    ctx = make_ctx(buyers=BUYERS)
    wf = ContactWaterfall(ctx)
    from leadgen.contacts import _Slot  # the slot type is internal; build one directly
    wf.slots.append(_Slot("weird", Weird({}, ctx)))
    company = Company(name="Acme")
    assert wf.enrich(company) == ["weird"]
    assert [(c.full_name, c.source) for c in company.contacts] == [("Dict Person", "weird")]


def test_missing_credential_error_type_is_what_secret_raises(make_ctx):
    with pytest.raises(MissingCredentialError):
        FakeFinder({"api_key_env": "NOPE"}, make_ctx()).secret()


# --- regressions (verifier findings) -----------------------------------------------------

from leadgen.contacts import fill_contact, merge_contacts  # noqa: E402


class NetErrorFinder(ContactFinder):
    """Fails like requests does when the host is unreachable: the message quotes the full URL."""

    name = "fake_neterr"

    def find(self, company: Company) -> List[Contact]:
        raise HttpError(0, "https://api.fake.test/v2/domain-search?domain=acme.com&api_key=hk_SECRET_123",
                        "ConnectionError: Max retries exceeded with url: /v2/domain-search?"
                        "domain=acme.com&limit=10&api_key=hk_SECRET_123 (Caused by NewConnectionError())")


registry.register("finder", "fake_neterr", "tests.test_contacts:NetErrorFinder")


def test_waterfall_errors_never_contain_api_keys(make_ctx, caplog):
    ctx = make_ctx(buyers=BUYERS, enrichment=finders({"type": "fake_neterr", "label": "hunter"}))
    errors: List[str] = []
    with caplog.at_level(logging.ERROR, logger="leadgen.test"):
        ContactWaterfall(ctx, errors=errors).enrich(Company(name="Acme", domain="acme.com"))
    assert len(errors) == 1 and errors[0].startswith("finder hunter: HTTP 0")
    assert "hk_SECRET_123" not in errors[0] and "api_key=***" in errors[0]
    assert "hk_SECRET_123" not in caplog.text


EXCLUDED = {"exclude_domains": ["bigclient.com"]}


def test_select_drops_contacts_at_excluded_domains(make_ctx):
    ctx = make_ctx(buyers=BUYERS, icp=EXCLUDED)
    company = Company(name="BigClient", contacts=[
        Contact(full_name="Jane Doe", title="CFO", email="jane@bigclient.com", email_status="valid"),
        Contact(full_name="Vic", title="VP Finance", email="vic@eu.bigclient.com", email_status="valid"),
        Contact(full_name="Cara", title="Controller",
                email_candidates=["cara@bigclient.com", "cara@bigclient-group.com"]),
    ])
    chosen = select_contacts(company, ctx, limit=None)
    assert [(c.full_name, c.email, c.email_candidates) for c in chosen] == [
        ("Cara", "", ["cara@bigclient-group.com"])]
    assert company.contacts[2].email_candidates == ["cara@bigclient.com", "cara@bigclient-group.com"]


def test_waterfall_excluded_domain_email_is_not_reachable(make_ctx):
    ctx = make_ctx(buyers=BUYERS, icp=EXCLUDED, enrichment=finders(
        {"type": "fake_x", "label": "first", "people": [person("Jane Doe", "CFO", "jane@bigclient.com",
                                                                email_status="valid")]},
        {"type": "fake_x", "label": "second", "people": [person("Vic", "VP Finance", "vic@acme.com")]},
    ))
    wf = ContactWaterfall(ctx)
    company = Company(name="Acme")
    assert wf.enrich(company) == ["first", "second"]
    assert ("complete", "Jane Doe") not in wf.finders[0].calls  # no lookups for an excluded person
    assert [c.email for c in select_contacts(company, ctx, limit=None)] == ["vic@acme.com"]


def test_waterfall_skips_and_stops_at_excluded_company_domains(make_ctx):
    ctx = make_ctx(buyers=BUYERS, icp=EXCLUDED, enrichment=finders(
        {"type": "fake_x", "label": "first"}, {"type": "fake_x", "label": "second"}))
    wf = ContactWaterfall(ctx)
    assert wf.enrich(Company(name="BigClient", domain="eu.bigclient.com")) == []
    resolved = Company(name="BigClient")

    class Resolver(FakeFinder):
        def find(self, company: Company) -> List[Contact]:
            company.data["email_domain"] = "bigclient.com"  # as the Hunter finder does
            return []

    wf.slots[0].finder = Resolver({}, ctx)
    assert wf.enrich(resolved) == ["first"]
    assert wf.finders[1].calls == []


def test_pipeline_never_hands_over_contact_at_excluded_domain(make_ctx, tmp_path):
    from leadgen.models import Signal
    from leadgen.pipeline import Pipeline
    from tests.conftest import TODAY

    ctx = make_ctx(icp=EXCLUDED, buyers={"titles": ["CFO"]}, enrichment=finders(
        {"type": "fake_x", "people": [person("Jane Doe", "CFO", "jane@bigclient.com", email_status="valid")]}))

    class P(Pipeline):
        def collect(self):
            return [Company(name="BigClient", signals=[Signal(type="job_posting", title="Accountant",
                                                              posted_at=TODAY)])]

    p = P(ctx, out_dir=tmp_path)
    result = p.run()
    assert all(ld.contact is None or "bigclient.com" not in ld.contact.email for ld in result.leads)
    assert p.outbound_leads(result.leads) == []


def test_fill_contact_replaces_generic_or_invalid_email():
    target = Contact(full_name="Jane Doe", title="CFO", email="careers@acme.com")
    fill_contact(target, Contact(full_name="Jane Doe", email="jane.doe@acme.com", email_status="valid"))
    assert (target.email, target.email_status) == ("jane.doe@acme.com", "valid")
    target = Contact(full_name="Jane Doe", email="jd@acme.com", email_status="invalid")
    fill_contact(target, Contact(full_name="Jane Doe", email="jane.doe@acme.com", email_status="risky"))
    assert (target.email, target.email_status) == ("jane.doe@acme.com", "risky")


def test_fill_contact_keeps_usable_email_and_records_other_as_candidate():
    target = Contact(full_name="Jane Doe", email="jane@acme.com")
    fill_contact(target, Contact(full_name="Jane Doe", email="jane.doe@acme.com", email_status="valid"))
    assert target.email == "jane@acme.com" and target.email_candidates == ["jane.doe@acme.com"]
    # an INVALID or generic source address is not worth keeping, nor one at another
    # domain (a stale address at a previous employer must never be verified and used)
    fill_contact(target, Contact(email="jd@acme.com", email_status="invalid"))
    fill_contact(target, Contact(email="info@acme.com"))
    fill_contact(target, Contact(email="jane@oldjob.com", email_status="valid"))
    assert target.email_candidates == ["jane.doe@acme.com"]
    # the same address never upgrades its own status
    target = Contact(email="jd@acme.com", email_status="invalid")
    fill_contact(target, Contact(email="jd@acme.com", email_status="valid"))
    assert target.email_status == "invalid"


def test_fill_contact_respects_allow_generic(make_ctx):
    ctx = make_ctx(buyers={"allow_generic_emails": True})
    target = Contact(full_name="Jane Doe", email="finance@acme.com")
    fill_contact(target, Contact(email="jane.doe@acme.com"), ctx)
    assert target.email == "finance@acme.com" and target.email_candidates == ["jane.doe@acme.com"]


def test_waterfall_finder_email_replaces_generic_address_of_same_person(make_ctx):
    ctx = make_ctx(buyers={"titles": ["CFO"]}, enrichment=finders(
        {"type": "fake_x", "people": [person("Jane Doe", "CFO", "jane.doe@acme.com", email_status="valid",
                                             linkedin_url="https://www.linkedin.com/in/janedoe")]},
        {"type": "fake_x", "label": "never"},
    ))
    wf = ContactWaterfall(ctx)
    company = Company(name="Acme", domain="acme.com", contacts=[
        Contact(full_name="Jane Doe", title="CFO", email="careers@acme.com",
                linkedin_url="https://linkedin.com/in/janedoe")])
    assert wf.enrich(company) == ["fake_x"]  # satisfied after the first finder
    assert [(c.email, c.email_status) for c in company.contacts] == [("jane.doe@acme.com", "valid")]
    assert [c.email for c in select_contacts(company, ctx)] == ["jane.doe@acme.com"]


def test_waterfall_completes_target_with_generic_or_invalid_email(make_ctx):
    ctx = make_ctx(buyers={"titles": ["CFO", "Controller"]}, enrichment=finders(
        {"type": "fake_x", "emails": {"Jane Doe": "jane.doe@acme.com", "Val": "val@acme.com"},
         "status": "valid"}))
    wf = ContactWaterfall(ctx)
    company = Company(name="Acme", domain="acme.com", contacts=[
        Contact(full_name="Jane Doe", title="CFO", email="jobs@acme.com")])
    wf.enrich(company)
    assert ("complete", "Jane Doe") in wf.finders[0].calls
    assert company.contacts[0].email == "jane.doe@acme.com"
    company = Company(name="Acme", domain="acme.com", contacts=[
        Contact(full_name="Val", title="Controller", email="v@acme.com", email_status="invalid")])
    wf.enrich(company)
    assert (company.contacts[0].email, company.contacts[0].email_status) == ("val@acme.com", "valid")


def test_waterfall_pattern_finder_guesses_for_generic_address(make_ctx):
    ctx = make_ctx(buyers={"titles": ["CFO"]}, enrichment=finders({"type": "pattern"}))
    company = Company(name="Acme", domain="acme.com", contacts=[
        Contact(full_name="Jane Doe", title="CFO", email="careers@acme.com")])
    ContactWaterfall(ctx).enrich(company)
    chosen = select_contacts(company, ctx)
    assert chosen[0].email == "" and "jane.doe@acme.com" in chosen[0].email_candidates


def test_merge_contacts_passes_ctx(make_ctx):
    ctx = make_ctx(icp=EXCLUDED)
    company = Company(name="Acme", contacts=[
        Contact(full_name="Jane", email="jane@bigclient.com", linkedin_url="https://linkedin.com/in/j")])
    merge_contacts(company, [Contact(full_name="Jane", email="jane@acme.com",
                                     linkedin_url="https://linkedin.com/in/j")], ctx)
    assert company.contacts[0].email == "jane@acme.com"


@pytest.mark.parametrize("title", [
    "Founder's Associate", "Founders Associate", "Founders' Associate", "Former CEO", "Ex-CEO",
    "Deputy CEO", "CEO's Office Manager", "CEO Office Manager", "Chief of Staff, Office of the CEO",
    "Associate Founder", "Retired Founder",
])
def test_titles_that_belong_to_someone_else_do_not_rank(make_ctx, title):
    ctx = make_ctx(buyers={"titles": ["Founder", "CEO"]})
    assert title_rank(title, ctx) is None


@pytest.mark.parametrize("title,rank", [
    ("Founder & CEO", 0), ("CEO & Founder", 0), ("Co-Founder", 0), ("Founder, ex-Google", 0),
    ("CEO, former CFO", 1), ("Interim CEO", 1), ("Acting CEO", 1), ("Chief Executive Officer", 1),
])
def test_qualified_titles_still_rank(make_ctx, title, rank):
    ctx = make_ctx(buyers={"titles": ["Founder", "CEO"]})
    assert title_rank(title, ctx) == rank


@pytest.mark.parametrize("title,wanted,expected", [
    ("Former Director of Finance", "Finance Director", False),
    ("Associate Director, Finance", "Finance Director", False),
    ("Finance Director's Assistant", "Finance Director", False),
    ("Deputy CFO", "CFO", False),
    ("Vice Chair", "Chair", False),
    ("Director, Children's Services", "Director", True),
    ("Director of Finance", "Finance Director", True),
    ("Director, Office of Finance", "Finance Director", True),
    ("Director, Office of the CFO", "CFO", False),
    ("Deputy CFO", "Deputy CFO", True),
])
def test_title_qualifiers(title, wanted, expected):
    assert title_matches(title, wanted) is expected


def test_select_prefers_real_founder_over_founders_associate(make_ctx):
    ctx = make_ctx(buyers={"titles": ["Founder", "CEO"]})
    company = Company(name="Acme", domain="acme.com", contacts=[
        Contact(full_name="Sam Lee", title="Founder & CEO"),
        Contact(full_name="Alex Kim", title="Founder's Associate", email="alex@acme.com", email_status="valid"),
    ])
    assert select_contacts(company, ctx)[0].full_name == "Sam Lee"
    assert not ContactWaterfall(ctx).satisfied(company)
