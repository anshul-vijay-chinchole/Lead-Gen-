"""The shipped delivery playbooks, the demo client and the offline sample data.

* every shipped playbook (outbound ones included) and every client file in
  ``clients/`` loads, and every adapter type it names exists;
* ``recruitment-delivery`` and ``demo-delivery`` run in delivery mode and use
  no "use at own risk" scraper and no hand-over exporter; ``recruitment-delivery``
  is zero-budget as shipped (every paid tool is present but switched off);
* ``my-agency`` / ``demo-offline`` stay outbound;
* ``clients/demo-client.yaml`` produces a real delivery offline (>= 8 leads)
  and the ledger makes the next delivery smaller;
* the sample CSVs are fictional, never go stale (relative dates) and use
  headers the csv source / csv finder recognise automatically.
"""
from __future__ import annotations

import csv
import dataclasses
import logging
import re
from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pytest

from leadgen import cli, registry
from leadgen.context import Context
from leadgen.delivery.client import Client, client_playbook, list_clients, load_client
from leadgen.delivery.ledger import Ledger, LedgerHooks, lead_items
from leadgen.delivery.rows import EMAIL_LABELS, GUESSED, NOT_FOUND, RISKY, VERIFIED, email_label
from leadgen.models import Company, Contact, Signal, SignalType
from leadgen.pipeline import Pipeline, RunResult
from leadgen.playbook import Playbook, load_playbook
from leadgen.scoring import score, tier_for
from leadgen.sources.csv_source import CsvSource
from leadgen.sources.mapping import detect_mapping
from leadgen.store import Store
from tests.conftest import REPO_ROOT, SHIPPED_PLAYBOOKS, TODAY
from tests.fakes import FakeHttp

PLAYBOOKS = REPO_ROOT / "playbooks"
CLIENTS = REPO_ROOT / "clients"
DATA = REPO_ROOT / "examples" / "data"
RECRUITMENT_DELIVERY = PLAYBOOKS / "recruitment-delivery.yaml"
DEMO_DELIVERY = PLAYBOOKS / "demo-delivery.yaml"
DELIVERY_PLAYBOOKS = [RECRUITMENT_DELIVERY, DEMO_DELIVERY]
ALL_PLAYBOOKS = list(SHIPPED_PLAYBOOKS) + DELIVERY_PLAYBOOKS
DEMO_JOBS = DATA / "demo_jobs.csv"
DEMO_CONTACTS = DATA / "demo_delivery_contacts.csv"

# the demo client's first delivery (leads_per_week: 10) and the two held back for the next one
FIRST_DELIVERY = {
    "Hudson Yards Media", "Lonestar Freight Co", "Kingsbridge Logistics Ltd", "Lakeshore Manufacturing",
    "Riverbend Health Partners", "Pecan Street Software", "Magnolia Home Goods", "Prairie Mutual Insurance",
    "Northern Quarter Games", "Clydeside Engineering Ltd",
}
HELD_BACK = {"Peachtree Dental Group", "Empire Solar"}


# --- helpers -------------------------------------------------------------------------------------------

@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


@pytest.fixture
def at_repo_root(monkeypatch):
    """Shipped playbooks use paths relative to the repository root (like the README's commands)."""
    monkeypatch.chdir(REPO_ROOT)
    return REPO_ROOT


def adapter_configs(pb: Playbook) -> List[Tuple[str, Dict[str, Any]]]:
    """(kind, config) for every adapter a playbook names, switched off or not."""
    out: List[Tuple[str, Dict[str, Any]]] = [("source", s) for s in pb.sources]
    out += [("finder", f) for f in pb.enrichment.get("finders") or []]
    if pb.enrichment.get("verifier"):
        out.append(("verifier", pb.enrichment["verifier"]))
    out += [("exporter", e) for e in pb.outbound.get("exporters") or []]
    out += [("notifier", n) for n in pb.notify.get("channels") or []]
    return out


def enabled(configs: Iterable[Tuple[str, Dict[str, Any]]]) -> List[Tuple[str, Dict[str, Any]]]:
    return [(k, c) for k, c in configs if c.get("enabled") is not False]


def read_csv(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def context_for(pb: Playbook, store: Store, http: Optional[FakeHttp] = None, dry_run: bool = False,
             env: Optional[Dict[str, str]] = None) -> Context:
    return Context(playbook=pb, http=http or FakeHttp(), store=store, env=dict(env or {}), today=TODAY,
                   log=logging.getLogger("leadgen.test"), dry_run=dry_run)


def demo_client() -> Client:
    return load_client("demo-client", clients_dir=str(CLIENTS))


def run_client(client: Client, store: Store, out: Path, dry_run: bool = False,
               http: Optional[FakeHttp] = None) -> Tuple[RunResult, LedgerHooks, FakeHttp]:
    pb = client_playbook(client, env={})
    http = http or FakeHttp()
    hooks = LedgerHooks(Ledger(store), client, TODAY)
    res = Pipeline(context_for(pb, store, http, dry_run=dry_run), out_dir=out, hooks=hooks).run()
    return res, hooks, http


def deliverable(res: RunResult, client: Client) -> List[Any]:
    """Leads the client could receive: a tier it takes and a live job posting, best first."""
    leads = [ld for ld in res.leads if ld.tier in client.tiers
             and any(s.type == SignalType.JOB_POSTING for s in ld.company.signals)]
    return sorted(leads, key=lambda ld: (ld.tier != "hot", -ld.score, ld.company.name))


# --- every shipped file loads -----------------------------------------------------------------------------

@pytest.mark.parametrize("path", ALL_PLAYBOOKS, ids=lambda p: p.name)
def test_every_shipped_playbook_loads_and_names_known_adapters(path):
    pb = load_playbook(str(path), env={})
    assert pb.name and pb.description
    for kind, cfg in adapter_configs(pb):
        registry.resolve(kind, cfg["type"])          # raises for an unknown type


@pytest.mark.parametrize("path", DELIVERY_PLAYBOOKS, ids=lambda p: p.name)
def test_delivery_playbook_notify_on_is_read_from_the_file(path, tmp_path):
    """An unquoted YAML ``on:`` key would silently become ``True``; the file quotes it."""
    text = path.read_text(encoding="utf-8")
    assert re.search(r'(?m)^  "on": \[run_summary\]', text)
    edited = text.replace('"on": [run_summary]', '"on": [run_summary, positive]')
    tmp = tmp_path / path.name
    tmp.write_text(edited, encoding="utf-8")
    assert load_playbook(str(tmp), env={}).notify["on"] == ["run_summary", "positive"]


@pytest.mark.parametrize("path", DELIVERY_PLAYBOOKS, ids=lambda p: p.name)
def test_leadgen_validate_accepts_the_delivery_playbooks(path, at_repo_root, tmp_path, capsys):
    env_file = tmp_path / "empty.env"
    env_file.write_text("", encoding="utf-8")
    code = cli.main(["validate", "-p", str(path.relative_to(REPO_ROOT)), "--db", str(tmp_path / "v.db"),
                     "--env-file", str(env_file)])
    out = capsys.readouterr().out
    assert not re.search(r"unknown \w+ type", out) and "cannot be created" not in out
    assert "file not found" not in out
    if path == DEMO_DELIVERY:
        assert code == 0 and "[FAIL]" not in out          # offline: nothing to set up
    else:
        assert code in (0, 1)                             # only the free Adzuna key can be missing
        fails = [line for line in out.splitlines() if line.lstrip().startswith("[FAIL]")]
        assert all("adzuna" in line and "ADZUNA_APP" in line for line in fails), fails


def test_leadgen_run_with_the_demo_delivery_playbook(at_repo_root, tmp_path, capsys):
    env_file = tmp_path / "empty.env"
    env_file.write_text("", encoding="utf-8")
    out_dir = tmp_path / "out"
    code = cli.main(["run", "-p", "playbooks/demo-delivery.yaml", "--out", str(out_dir),
                     "--db", str(tmp_path / "demo.db"), "--env-file", str(env_file)])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "delivery mode" in out
    [run_dir] = [d for d in out_dir.iterdir() if d.is_dir()]
    assert (run_dir / "opportunities.csv").exists() and (run_dir / "rejected.csv").exists()
    assert not list(run_dir.glob("*upload*"))               # nothing prepared for a sending tool


def test_delivery_playbook_names_are_unique():
    names = [load_playbook(str(p), env={}).name for p in ALL_PLAYBOOKS]
    assert len(names) == len(set(names))
    assert {"recruitment-delivery", "demo-delivery"} <= set(names)


def test_every_client_file_loads_and_builds_a_delivery_playbook(at_repo_root):
    """Doubles as a check of your own client files: a broken one fails here (and in `leadgen deliver`)."""
    names = list_clients(str(CLIENTS))
    assert "demo-client" in names and not any(n.startswith("_") for n in names)
    for name in names:
        client = load_client(name, clients_dir=str(CLIENTS))
        pb = client_playbook(client, env={})
        assert pb.mode == "delivery" and pb.name == f"client-{name}", name
        assert pb.buyers["max_contacts_per_company"] == 1, name
        for kind, cfg in adapter_configs(pb):
            registry.resolve(kind, cfg["type"])


def test_the_client_template_now_builds_on_recruitment_delivery(at_repo_root):
    client = load_client(str(CLIENTS / "_template.yaml"))
    assert client.playbook == "playbooks/recruitment-delivery.yaml"
    pb = client_playbook(client, env={})
    assert pb.mode == "delivery" and pb.path is not None and pb.path.name == "recruitment-delivery.yaml"
    assert [s["type"] for s in pb.sources if s.get("enabled") is not False] == ["adzuna"]
    assert pb.signals["max_age_days"] == 7 and pb.enrichment["max_companies"] == 50   # 25 leads x 2


# --- modes and safety -------------------------------------------------------------------------------------

@pytest.mark.parametrize("path", DELIVERY_PLAYBOOKS, ids=lambda p: p.name)
def test_delivery_playbooks_use_no_risky_or_handover_adapter(path):
    pb = load_playbook(str(path), env={})
    assert pb.mode == "delivery"
    for kind, cfg in adapter_configs(pb):          # switched-off entries count too
        assert registry.risk_note(kind, cfg["type"], cfg) == "", (kind, cfg)
        if kind == "exporter":
            assert getattr(registry.resolve(kind, cfg["type"]), "scope", "all") != "outbound", cfg
    types = {cfg["type"] for _, cfg in adapter_configs(pb)}
    assert not types & {"linkedin_jobs", "apify", "instantly", "instantly_csv", "smartlead",
                        "smartlead_csv", "webhook"}


def test_recruitment_delivery_explains_what_it_leaves_out():
    text = RECRUITMENT_DELIVERY.read_text(encoding="utf-8")
    comments = "\n".join(line for line in text.splitlines() if line.lstrip().startswith("#"))
    assert "LinkedIn" in comments and "Indeed" in comments
    assert "use at your own risk" in comments.lower()


@pytest.mark.parametrize("name", ["my-agency.yaml", "demo-offline.yaml"])
def test_outbound_playbooks_stay_outbound(name):
    assert load_playbook(str(PLAYBOOKS / name), env={}).mode == "outbound"


# --- recruitment-delivery: zero budget, hiring-signal defaults ------------------------------------------------

def test_recruitment_delivery_is_zero_budget_as_shipped():
    pb = load_playbook(str(RECRUITMENT_DELIVERY), env={})
    on = enabled(adapter_configs(pb))
    assert not [(k, c["type"]) for k, c in on if (k, c["type"]) in registry.PAID]
    off = {(k, c["type"]) for k, c in adapter_configs(pb) if c.get("enabled") is False}
    # the paid tools are there, ready to switch on
    assert {("source", "theirstack"), ("finder", "apollo"), ("finder", "hunter")} <= off
    assert {("source", "greenhouse"), ("source", "lever"), ("source", "ashby"), ("source", "csv")} <= off
    assert [c["type"] for k, c in on if k == "source"] == ["adzuna"]
    assert [c["type"] for k, c in on if k == "finder"] == ["pattern"]
    assert pb.enrichment["verifier"] == {"type": "basic"}
    assert pb.usage["max_paid_lookups"] == 0 and pb.usage["cost_per_call"] == {}


def test_recruitment_delivery_sources_are_filled_in_correctly():
    pb = load_playbook(str(RECRUITMENT_DELIVERY), env={})
    by_type = {s["type"]: s for s in pb.sources}
    assert by_type["adzuna"]["countries"] == ["us"] and by_type["adzuna"]["queries"]
    assert by_type["adzuna"]["max_days_old"] == pb.signals["max_age_days"] == 7
    assert by_type["csv"]["path"] == "data/imports/jobs.csv"
    for ats in ("greenhouse", "lever", "ashby"):
        entries = by_type[ats]["companies"]
        assert entries and all(e.get("name") and e.get("board") for e in entries), ats
    assert by_type["theirstack"]["job_titles"]


def test_recruitment_delivery_signal_buyer_and_scoring_defaults():
    pb = load_playbook(str(RECRUITMENT_DELIVERY), env={})
    sig = pb.signals
    assert sig["primary"] == ["job_posting"] and sig["require"] is True
    assert (sig["max_age_days"], sig["drop_reposts"], sig["allow_undated"]) == (7, True, False)
    titles = [t.lower() for t in pb.buyers["titles"]]
    assert titles[0] == "head of talent acquisition"
    assert {"hr director", "hiring manager", "director", "head", "vp"} <= set(titles)
    assert pb.outbound["exporters"] == [{"type": "csv"}]
    s = pb.scoring
    assert sum(s["weights"].values()) == 100 and 0 < s["tiers"]["normal"] < s["tiers"]["hot"] <= 100
    assert s["freshness_days"][-2] == 7           # the bands fit a 7-day window


def test_recruitment_delivery_branding_comes_from_env():
    pb = load_playbook(str(RECRUITMENT_DELIVERY), env={})
    assert pb.delivery["brand_name"] == "Hiring Signal Report"
    assert pb.delivery["sender_name"] == "Your Name" and pb.delivery["sender_email"] == ""
    pb = load_playbook(str(RECRUITMENT_DELIVERY), env={"SENDER_NAME": "Sam Lee",
                                                       "SENDER_EMAIL": "sam@report-demo.com",
                                                       "SENDER_WEBSITE": "https://report-demo.com"})
    assert (pb.delivery["sender_name"], pb.delivery["sender_email"], pb.delivery["website"]) == \
        ("Sam Lee", "sam@report-demo.com", "https://report-demo.com")


def test_recruitment_delivery_dry_run_makes_no_network_calls(at_repo_root, tmp_path, store):
    pb = load_playbook(str(RECRUITMENT_DELIVERY), env={})
    http = FakeHttp()                     # any request would fail the run with "unexpected HTTP request"
    ctx = context_for(pb, store, http, dry_run=True)
    res = Pipeline(ctx, out_dir=tmp_path).run()
    assert http.calls == [] and res.errors == []
    assert res.counts["sourced"] == 0 and res.leads == []


def _adzuna_job(job_id: str, title: str, company: str, created: str, location: str) -> Dict[str, Any]:
    """One result in Adzuna's documented search response shape."""
    return {
        "__CLASS__": "Adzuna::API::Response::Job", "id": job_id, "adref": "eyJhbGciOiJIUzI1NiJ9.eyJzIjoiMSJ9",
        "title": title, "description": f"We are hiring a <strong>{title}</strong> to join the finance team&hellip;",
        "created": created,
        "redirect_url": f"https://www.adzuna.com/details/{job_id}?utm_medium=api&utm_source=abc",
        "company": {"__CLASS__": "Adzuna::API::Response::Company", "display_name": company},
        "location": {"__CLASS__": "Adzuna::API::Response::Location", "display_name": location,
                     "area": ["US", "Texas", "Harris County", "Houston"]},
        "category": {"__CLASS__": "Adzuna::API::Response::Category", "label": "Accounting & Finance Jobs",
                     "tag": "accounting-finance-jobs"},
        "salary_min": 70000, "salary_max": 85000, "salary_is_predicted": "1",
        "contract_type": "permanent", "contract_time": "full_time",
    }


def test_recruitment_delivery_free_run_delivers_company_only_leads(at_repo_root, tmp_path, store):
    """The zero-budget default: Adzuna (free key) finds the jobs, nobody is named (no paid finder),
    and a fresh job at a fitting company is still a deliverable lead (tier normal, not skip)."""
    env = {"ADZUNA_APP_ID": "app-id-1", "ADZUNA_APP_KEY": "app-key-2"}
    pb = load_playbook(str(RECRUITMENT_DELIVERY), env=env)
    http = FakeHttp()
    page = {"__CLASS__": "Adzuna::API::Response::JobSearchResults", "count": 3, "mean": 76000.0, "results": [
        _adzuna_job("5001", "Senior Accountant", "Bayside Freight Inc", "2026-09-23T10:00:00Z", "Houston, TX"),
        _adzuna_job("5002", "Payroll Accountant", "Bayside Freight Inc", "2026-09-22T09:00:00Z", "Houston, TX"),
        # 7 days old, nobody named, size + industry unknown: still inside the weekly window
        _adzuna_job("5003", "Staff Accountant", "Metro Clinics", "2026-09-17T08:00:00Z", "Dallas, TX"),
        _adzuna_job("5004", "Accounting Intern", "Metro Clinics", "2026-09-23T08:00:00Z", "Dallas, TX"),
        _adzuna_job("5005", "Recruitment Consultant", "Lone Star Staffing Agency", "2026-09-23T08:00:00Z",
                    "Austin, TX"),
    ]}
    http.add("GET", re.compile(r"/jobs/us/search/1$"), json=page)
    ctx = context_for(pb, store, http, env=env)
    res = Pipeline(ctx, out_dir=tmp_path).run()
    assert res.errors == []
    # request shape: one search per query, US only, a 7-day window, credentials as query params
    assert len(http.calls) == 3
    assert {c["params"]["what"] for c in http.calls} == {"accountant", "financial controller", "finance manager"}
    for call in http.calls:
        assert call["url"] == "https://api.adzuna.com/v1/api/jobs/us/search/1"
        p = call["params"]
        assert (p["app_id"], p["app_key"], p["max_days_old"]) == ("app-id-1", "app-key-2", 7)
        assert p["what_exclude"] == "intern internship" and p["sort_by"] == "date"
    assert ctx.usage.paid_lookups == 0               # Adzuna is free
    by_name = {ld.company.name: ld for ld in res.leads}
    assert set(by_name) == {"Bayside Freight Inc", "Metro Clinics"}
    for ld in res.leads:
        assert ld.contact is None and ld.tier in ("hot", "normal"), (ld.company.name, ld.score)
    assert [s.title for s in by_name["Metro Clinics"].company.signals] == ["Staff Accountant"]   # intern dropped
    rejected = {r["company"]: r["reason"] for r in res.rejected}
    assert "Lone Star Staffing Agency" in rejected


@pytest.mark.parametrize("path", DELIVERY_PLAYBOOKS, ids=lambda p: p.name)
def test_delivery_scoring_tiers(path, make_ctx):
    """Tiers for a weekly report: nothing inside the freshness window is 'skip'; fresh jobs with a
    verified decision-maker are 'hot'; only stale jobs with nobody named fall to 'skip'."""
    pb = load_playbook(str(path), env={})
    ctx = make_ctx(mode="delivery", name="tiers", scoring=pb.scoring, signals=pb.signals,
                   buyers={"titles": ["CFO", "Controller"]},       # a client's buyer_titles
                   icp={"employees": {"min": 20, "max": None}, "exclude_industries": ["staffing"]})

    def lead(days_old: List[int], contact: Optional[Contact] = None, known: bool = True) -> str:
        company = Company(name="Tier Test Co", location="Dallas, TX", signals=[
            Signal(type="job_posting", title=f"Accountant {i}", posted_at=TODAY - timedelta(days=d),
                   external_id=str(i)) for i, d in enumerate(days_old)])
        if known:
            company.employees, company.industry = 120, "Logistics"
        return tier_for(score(company, contact, ctx).total, ctx)

    cfo = Contact(first_name="Ana", last_name="Ruiz", title="CFO", email="ana@tier-demo.com",
                  email_status="valid")
    guessed = Contact(first_name="Ana", last_name="Ruiz", title="Controller", email="ana.ruiz@tier-demo.com",
                      email_status="unknown", data={"email_guessed": True})
    assert lead([7], known=False) == "normal"      # oldest job in the window, no data, nobody named
    assert lead([0], known=False) == "normal"
    assert lead([6], guessed) == "normal"
    assert lead([1], cfo) == "hot"                 # fresh + verified decision-maker
    assert lead([0, 1, 2], cfo) == "hot"
    assert lead([0, 1, 2], guessed) == "hot"       # 3 fresh roles + a top-3 buyer, email only guessed
    assert lead([1, 2, 3]) == "normal"             # several fresh roles, nobody named yet
    assert lead([20], known=False) == "skip"       # a client allowing older jobs: stale + nobody named


# --- demo-delivery: 100% offline -------------------------------------------------------------------------------

def test_demo_delivery_is_fully_offline_and_resolves_its_files(at_repo_root):
    pb = load_playbook(str(DEMO_DELIVERY.relative_to(REPO_ROOT)), env={})
    assert pb.mode == "delivery" and pb.db_path == Path("data/demo-delivery.db")
    for kind, cfg in enabled(adapter_configs(pb)):
        assert getattr(registry.resolve(kind, cfg["type"]), "offline", False), (kind, cfg)
        if cfg.get("path"):
            assert pb.resolve_path(cfg["path"]).exists(), cfg
    assert pb.sources[0]["path"] == "examples/data/demo_jobs.csv"
    assert pb.enrichment["finders"][0]["path"] == "examples/data/demo_delivery_contacts.csv"
    assert [f["type"] for f in pb.enrichment["finders"]] == ["csv", "pattern"]
    assert pb.enrichment["verifier"] == {"type": "basic"}
    assert (pb.signals["max_age_days"], pb.signals["drop_reposts"], pb.signals["allow_undated"]) == (7, True, False)
    # same hiring-signal scoring as the live playbook
    assert pb.scoring == load_playbook(str(RECRUITMENT_DELIVERY), env={}).scoring


@pytest.mark.parametrize("dry_run", [False, True])
def test_demo_delivery_base_run_finds_finance_and_engineering_leads(at_repo_root, tmp_path, dry_run, store):
    pb = load_playbook(str(DEMO_DELIVERY), env={})
    http = FakeHttp()
    res = Pipeline(context_for(pb, store, http, dry_run=dry_run), out_dir=tmp_path).run()
    assert http.calls == [] and res.errors == [] and res.mode == "delivery"
    names = {ld.company.name for ld in res.leads}
    assert {"Copperline Robotics", "Bluegum Software", "Hudson Yards Media", "Maple Grove Foods",
            "Wattle & Co"} <= names                      # engineering + finance, US / UK / CA / AU
    assert len(res.leads) >= 15 and res.counts["hot"] >= 3
    assert all(not ld.messages for ld in res.leads)     # delivery mode never writes email copy
    rejected = {r["company"]: r["reason"] for r in res.rejected}
    assert "Summit Staffing Group" in rejected and "Alamo Craft Brewing" in rejected
    assert (res.out_dir / "opportunities.csv").exists()


# --- the demo client ----------------------------------------------------------------------------------------

def test_demo_client_settings():
    c = demo_client()
    assert c.display_name == "Northstar Finance Recruiting"
    assert c.playbook == "playbooks/demo-delivery.yaml"
    assert c.leads_per_week == 10 and c.freshness_days == 7
    assert list(c.delivery.formats) == ["csv", "xlsx", "html"]
    assert c.opening_line.enabled is True and c.opening_line.ai is False
    assert {"Texas", "United Kingdom"} <= set(c.locations)
    assert c.exclusions.companies and c.exclusions.domains and "staffing" in c.exclusions.keywords
    assert "accountant" in c.roles and "CFO" in c.buyer_titles
    assert c.tiers == ["hot", "normal"] and c.email_policy_include_unverified is True


@pytest.mark.parametrize("dry_run", [False, True])
def test_demo_client_pipeline_yields_leads_offline(at_repo_root, tmp_path, dry_run, store):
    client = demo_client()
    res, hooks, http = run_client(client, store, tmp_path, dry_run=dry_run)
    assert http.calls == [] and res.errors == [] and res.mode == "delivery"
    leads = deliverable(res, client)
    assert len(leads) >= 8
    names = {ld.company.name for ld in leads}
    assert FIRST_DELIVERY | HELD_BACK == names
    # every email label shows up, and guesses are never called verified
    labels = Counter(email_label(ld.contact) for ld in leads)
    assert set(labels) == set(EMAIL_LABELS)
    assert labels[VERIFIED] >= 4 and labels[RISKY] >= 2 and labels[GUESSED] >= 2 and labels[NOT_FOUND] >= 1
    for ld in leads:
        if ld.contact is not None and ld.contact.data.get("email_guessed"):
            assert email_label(ld.contact) == GUESSED
    by_name = {ld.company.name: ld for ld in leads}
    assert by_name["Clydeside Engineering Ltd"].contact.linkedin_url    # no website: LinkedIn, no email
    assert email_label(by_name["Clydeside Engineering Ltd"].contact) == NOT_FOUND
    # the decision-maker is the client's buyer, not whoever has the surest email
    assert by_name["Pecan Street Software"].contact.title == "VP of Finance"
    # the re-posted Cost Accountant ad: only the fresh one is live
    lakeshore = by_name["Lakeshore Manufacturing"].company.signals
    assert sorted(s.external_id for s in lakeshore) == ["LM-131", "LM-140"]
    assert res.counts["hot"] >= 3
    reasons = {r["company"]: r["reason"] for r in res.rejected}
    assert "too small" in reasons["Alamo Craft Brewing"]
    assert "staffing" in reasons["Summit Staffing Group"]
    assert "excluded domain" in reasons["Gulf Coast Paper Co"]
    assert "do-not-list" in reasons["Riverwalk Hospitality Group"]
    for company in ("Bayou Energy Services", "Maple Grove Foods", "Wattle & Co", "Copperline Robotics"):
        assert "location" in reasons[company], company
    assert "older than 7 days" in reasons["Old Mill Bakeries"]
    assert "without a posting date" in reasons["Greyfriars Legal LLP"]
    assert "intern" in reasons["Lone Pine Credit Union"]
    assert hooks.removed["suppressed"] == 1


def test_demo_client_second_week_brings_only_what_was_held_back(at_repo_root, tmp_path, store):
    client = demo_client()
    ledger = Ledger(store)
    res1, _, _ = run_client(client, store, tmp_path)
    first = deliverable(res1, client)[: client.leads_per_week]
    assert {ld.company.name for ld in first} == FIRST_DELIVERY
    for ld in first:
        ledger.record(client.name, lead_items(ld), res1.run_id, TODAY)
    res2, hooks2, _ = run_client(client, store, tmp_path)
    second = deliverable(res2, client)
    assert {ld.company.name for ld in second} == HELD_BACK
    assert 0 < len(second) < len(first)
    assert hooks2.removed["company"] == len(FIRST_DELIVERY)


@pytest.mark.parametrize("change, company", [
    ({"allow_undated": True}, "Greyfriars Legal LLP"),       # the undated job
    ({"freshness_days": 30}, "Old Mill Bakeries"),           # jobs 12 and 16 days old
    ({"locations": []}, "Maple Grove Foods"),                # anywhere: Canada too
])
def test_demo_data_reacts_to_the_client_settings(at_repo_root, tmp_path, change, company, store):
    client = demo_client()
    before, _, _ = run_client(client, store, tmp_path / "a")
    assert company not in {ld.company.name for ld in before.leads}
    after, _, _ = run_client(dataclasses.replace(client, **change), store, tmp_path / "b")
    assert company in {ld.company.name for ld in after.leads}


def test_demo_client_full_delivery_with_files(at_repo_root, tmp_path, store):
    run = pytest.importorskip("leadgen.delivery.run")
    client = demo_client()
    kw = dict(store=store, env={}, http=FakeHttp(), today=TODAY)
    preview = run.deliver(client, dry_run=True, out_dir=tmp_path / "preview", **kw)
    assert preview.delivered == client.leads_per_week and preview.recorded == 0
    assert all("PREVIEW" in p.name and p.exists() for p in preview.files.values())

    week1 = run.deliver(client, out_dir=tmp_path / "week1", **kw)
    assert week1.delivered >= 8 and week1.recorded > 0
    assert set(week1.files) == {"csv", "xlsx", "html"} and all(p.exists() for p in week1.files.values())
    assert {r["company"] for r in week1.package.rows} == FIRST_DELIVERY
    assert all(r.get("opening_line") for r in week1.package.rows)          # template lines, no AI
    assert set(week1.package.counts_by_email_status) == set(EMAIL_LABELS)
    assert all(v > 0 for v in week1.package.counts_by_email_status.values())

    week2 = run.deliver(client, out_dir=tmp_path / "week2", **kw)
    assert 0 < week2.delivered < week1.delivered
    assert {r["company"] for r in week2.package.rows} == HELD_BACK

    week3 = run.deliver(client, out_dir=tmp_path / "week3", **kw)
    assert week3.delivered == 0


# --- the sample data ------------------------------------------------------------------------------------------

_RELATIVE_DATE = re.compile(r"^(today|yesterday|\d+ days? ago)$")


def _hosts(text: str) -> set:
    return set(re.findall(r"@([a-z0-9.-]+\.[a-z]{2,})", text)) | set(re.findall(r"https?://([a-z0-9.-]+)", text))


@pytest.mark.parametrize("path", [DEMO_JOBS, DEMO_CONTACTS], ids=lambda p: p.name)
def test_sample_data_is_fictional(path):
    text = path.read_text(encoding="utf-8")
    hosts = _hosts(text)
    real = sorted(h for h in hosts if not h.endswith("-demo.com"))
    assert hosts and not real, real            # made-up domains only - never a real company or person
    _, rows = read_csv(path)
    for row in rows:
        domain = row.get("Domain") or ""
        assert not domain or domain.endswith("-demo.com"), row


def test_demo_jobs_never_go_stale_and_are_auto_detected():
    headers, rows = read_csv(DEMO_JOBS)
    assert 35 <= len(rows) <= 45
    companies = {r["Company"] for r in rows}
    assert 20 <= len(companies) <= 30
    for r in rows:
        for col in ("Date Posted", "Funding Date"):
            assert not r[col] or _RELATIVE_DATE.match(r[col]), (col, r[col])
    mapping, info = detect_mapping(headers)
    assert info["mode"] == "jobs" and info["unmapped"] == []
    expect = {"name": "Company", "domain": "Domain", "location": "Location", "industry": "Industry",
              "employees": "Employees", "signal_title": "Job Title", "signal_url": "Job URL",
              "signal_date": "Date Posted", "signal_id": "Job ID", "signal_description": "Job Description",
              "funding_stage": "Latest Funding", "funding_amount": "Funding Amount", "funding_date": "Funding Date"}
    for field, header in expect.items():
        assert mapping.get(field) == [header], field


def test_demo_jobs_cover_every_scenario(make_ctx):
    ctx = make_ctx(mode="delivery", name="demo-data-check")
    companies = CsvSource({"type": "csv", "path": str(DEMO_JOBS)}, ctx).fetch()
    by_name = {c.name: c for c in companies}
    assert len(by_name) == len(companies)                            # rows grouped per company
    jobs = [(c, s) for c in companies for s in c.signals if s.type == SignalType.JOB_POSTING]
    funding = [s for c in companies for s in c.signals if s.type == SignalType.FUNDING]
    assert len(jobs) == 40 and len(funding) >= 3
    ages = [s.age_days(TODAY) for _, s in jobs]
    assert sum(1 for a in ages if a is None) >= 1                    # undated
    assert sum(1 for a in ages if a is not None and a > 7) >= 4       # too old for the 7-day window
    assert sum(1 for a in ages if a is not None and a <= 2) >= 10     # plenty of fresh ones
    # a re-posted ad: same company + title, two job ids, one of them old
    per_title = Counter((c.name, s.title) for c, s in jobs)
    repost = [key for key, n in per_title.items() if n > 1]
    assert repost == [("Lakeshore Manufacturing", "Cost Accountant")]
    ids = {s.external_id: s.age_days(TODAY) for c, s in jobs if (c.name, s.title) == repost[0]}
    assert ids == {"LM-131": 2, "LM-088": 24}
    # finance AND engineering roles; US states, UK, Canada, Australia; sizes from tiny to large
    titles = " ".join(s.title.lower() for _, s in jobs)
    assert "accountant" in titles and "engineer" in titles
    places = " ".join(c.location for c in companies)
    for place in (", TX", ", IL", ", NY", ", GA", "United Kingdom", "Scotland", "Canada", "Australia"):
        assert place in places, place
    sizes = [c.employees for c in companies if c.employees is not None]
    assert min(sizes) < 20 and max(sizes) >= 1000
    assert any("staffing" in c.industry.lower() for c in companies)   # a competitor to exclude
    assert [c.name for c in companies if not c.domain] == ["Clydeside Engineering Ltd", "Bluegum Software"]


def test_demo_contacts_mix_every_kind_of_email():
    headers, rows = read_csv(DEMO_CONTACTS)
    assert headers == ["Company", "Domain", "First Name", "Last Name", "Title", "Email", "Email Status",
                       "LinkedIn URL"]
    statuses = Counter(r["Email Status"] for r in rows if r["Email"])
    assert statuses["verified"] >= 8 and statuses["catch-all"] >= 2 and statuses["unverified"] >= 2
    no_email = [r for r in rows if not r["Email"]]
    assert len(no_email) >= 4 and all(r["First Name"] and r["Last Name"] for r in no_email)
    assert sum(1 for r in no_email if r["LinkedIn URL"]) >= 3           # LinkedIn only
    assert any(r["Email"].startswith("jobs@") and not r["First Name"] for r in rows)   # a shared mailbox
    job_companies = {r["Company"] for r in read_csv(DEMO_JOBS)[1]}
    contact_companies = {r["Company"] for r in rows}
    assert contact_companies <= job_companies
    assert len(contact_companies) >= 0.75 * len(job_companies)          # most companies have a decision-maker


# --- packaging ---------------------------------------------------------------------------------------------

def test_openpyxl_is_a_declared_dependency():
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    deps = re.search(r"(?m)^dependencies = \[(.*)\]$", pyproject)
    assert deps and "openpyxl" in deps.group(1)
    requirements = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").split()
    assert any(r.startswith("openpyxl") for r in requirements)
    import openpyxl  # noqa: F401 - installed, so the Excel format works
