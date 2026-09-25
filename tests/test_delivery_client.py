"""Client files: loading, validation, the client playbook and new client files."""
from __future__ import annotations

import textwrap
from datetime import date
from pathlib import Path

import pytest
import yaml

from leadgen.delivery.client import (
    CLIENT_KEYS,
    Budget,
    Client,
    ClientError,
    DeliverySettings,
    EmailPolicy,
    Exclusions,
    OpeningLine,
    SizeRange,
    client_playbook,
    is_handover_exporter,
    list_clients,
    load_client,
    new_client_file,
    playbook_name_for,
)
from leadgen.modes import is_outbound
from tests.conftest import REPO_ROOT

TEMPLATE = REPO_ROOT / "clients" / "_template.yaml"

BASE_PLAYBOOK = """\
name: base-delivery
mode: outbound
description: shared base for client deliveries
offer:
  sender_name: ${SENDER_NAME:-Nobody}
signals:
  match_keywords: [nurse, midwife]
  exclude_keywords: [intern, Volunteer]
buyers:
  titles: [Head of Nursing]
icp:
  locations: [Canada]
  employees: {min: 5, max: 50000}
  exclude_domains: [competitor.example]
  exclude_keywords: [staffing agency]
sources:
  - type: csv
    path: data/jobs.csv
enrichment:
  verifier: {type: basic}
writer:
  type: template
  provider: anthropic
  model: claude-haiku-4-5
outbound:
  exporters:
    - type: csv
    - type: json
    - type: instantly_csv
    - type: smartlead
    - type: webhook
      url: https://hooks.example.invalid/x
usage:
  max_paid_lookups: 40
delivery:
  brand_name: Base Brand
  brand_color: "#112233"
  footer: base footer
"""

FULL_CLIENT = """\
display_name: Acme Staffing Ltd
playbook: playbooks/base.yaml
contact: {name: Sam Lee, email: sam@acme-staffing.example}
roles: [accountant, controller, financial analyst]
exclude_roles: [intern, trainee]
buyer_titles: [CFO, VP Finance, Controller, Head of Talent]
locations: [Texas, Oklahoma]
exclude_locations: [Dallas]
company_size: {min: 20, max: 1000}
industries: [software]
exclusions:
  companies: [Their Existing Client Inc]
  domains: [https://www.BigClient.com/about]
  keywords: [staffing, recruiting]
leads_per_week: 30
freshness_days: 10
allow_undated: true
drop_reposts: false
redelivery_days: 90
dedupe: [job, contact]
tiers: [hot]
emails:
  include_unverified: false
opening_line:
  enabled: true
  ai: true
  max_cost_usd: 1.25
budget:
  max_paid_lookups: 150
delivery:
  formats: [xlsx, csv]
  folder: out/{client}/{date}
  google_sheet: {spreadsheet_id: abc123, worksheet: "Week of {date}"}
branding: {brand_name: Acme Weekly, brand_color: "#aa0000"}
overrides:
  scoring: {tiers: {hot: 75}}
"""


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """A workspace: tmp_path is the current folder, with playbooks/base.yaml and clients/."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "playbooks" / "data").mkdir(parents=True)
    (tmp_path / "playbooks" / "base.yaml").write_text(BASE_PLAYBOOK, encoding="utf-8")
    (tmp_path / "playbooks" / "data" / "jobs.csv").write_text("company,domain\n", encoding="utf-8")
    (tmp_path / "clients").mkdir()
    return tmp_path


def write_client(folder: Path, name: str, text: str) -> Path:
    path = folder / "clients" / f"{name}.yaml"
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def problem(ws: Path, text: str, name: str = "acme") -> str:
    write_client(ws, name, text)
    with pytest.raises(ClientError) as exc:
        load_client(name)
    return str(exc.value)


# --- loading ---------------------------------------------------------------------------------

def test_load_by_name_file_name_or_path(ws):
    path = write_client(ws, "acme", "leads_per_week: 12\n")
    for ref in ("acme", "acme.yaml", "clients/acme.yaml", str(path), "clients/acme"):
        c = load_client(ref)
        assert c.name == "acme" and c.leads_per_week == 12
        assert c.path is not None and c.path.resolve() == path.resolve()


def test_client_name_comes_from_the_file_on_a_case_insensitive_disk(ws, monkeypatch):
    """macOS / Windows: 'Acme' opens clients/acme.yaml - the client must still be 'acme'
    (the ledger's key for its delivery history and do-not-list, and its folder name)."""
    path = write_client(ws, "acme", "leads_per_week: 12\n")
    real_is_file = Path.is_file

    def case_insensitive_is_file(self):
        if real_is_file(self):
            return True
        try:
            return any(p.name.casefold() == self.name.casefold() and real_is_file(p) for p in self.parent.iterdir())
        except OSError:
            return False

    monkeypatch.setattr(Path, "is_file", case_insensitive_is_file)
    for ref in ("Acme", "ACME.yaml", "clients/Acme.YAML", "clients/aCmE"):
        c = load_client(ref)
        assert c.name == "acme" and c.path == path.relative_to(ws) and c.leads_per_week == 12, ref
        assert c.delivery_folder(date(2026, 9, 25)) == Path("deliveries/acme/2026-09-25")


def test_load_uses_clients_dir_and_yml_suffix(ws):
    other = ws / "elsewhere"
    other.mkdir()
    (other / "beta.yml").write_text("leads_per_week: 3\n", encoding="utf-8")
    assert load_client("beta", clients_dir=str(other)).leads_per_week == 3


def test_empty_file_gives_every_default(ws):
    write_client(ws, "acme", "")
    c = load_client("acme")
    assert c.display_name == "acme"
    assert c.playbook == "playbooks/recruitment-delivery.yaml"
    assert c.leads_per_week == 25 and c.freshness_days == 7
    assert c.allow_undated is False and c.drop_reposts is True
    assert c.redelivery_days is None
    assert c.dedupe == ["company", "job", "contact"] and c.tiers == ["hot", "normal"]
    assert c.email_policy_include_unverified is True
    assert c.opening_line == {"enabled": False, "ai": False, "max_cost_usd": 0.5}
    assert c.budget.max_paid_lookups == 0
    assert c.delivery.formats == ["csv", "xlsx", "html"]
    assert c.delivery.folder == "deliveries/{client}/{date}"
    assert c.delivery.google_sheet["spreadsheet_id"] == "" and c.delivery.google_sheet["worksheet"] == "{date}"
    assert c.company_size == {"min": None, "max": None}
    assert c.exclusions == {"companies": [], "domains": [], "keywords": []}
    assert c.branding == {} and c.overrides == {} and c.contact == {}
    # the same defaults as a client built in code
    built = Client("acme", path=c.path)
    assert c == built


def test_full_example_parses(ws):
    write_client(ws, "acme", FULL_CLIENT)
    c = load_client("acme")
    assert c.display_name == "Acme Staffing Ltd"
    assert c.contact == {"name": "Sam Lee", "email": "sam@acme-staffing.example"}
    assert c.roles == ["accountant", "controller", "financial analyst"]
    assert c.exclude_roles == ["intern", "trainee"]
    assert c.buyer_titles[0] == "CFO" and len(c.buyer_titles) == 4
    assert c.locations == ["Texas", "Oklahoma"] and c.exclude_locations == ["Dallas"]
    assert c.company_size.min == 20 and c.company_size["max"] == 1000
    assert c.industries == ["software"]
    assert c.exclusions.companies == ["Their Existing Client Inc"] == c.excluded_companies
    assert c.exclusions.domains == ["bigclient.com"]          # normalised
    assert c.exclusions.keywords == ["staffing", "recruiting"]
    assert (c.leads_per_week, c.freshness_days, c.allow_undated, c.drop_reposts) == (30, 10, True, False)
    assert c.redelivery_days == 90 and c.dedupe == ["job", "contact"] and c.tiers == ["hot"]
    assert c.email_policy_include_unverified is False
    assert c.opening_line.enabled and c.opening_line.ai and c.opening_line.max_cost_usd == 1.25
    assert c.budget.max_paid_lookups == 150
    assert c.delivery.formats == ["xlsx", "csv"] and c.delivery.folder == "out/{client}/{date}"
    assert c.delivery.google_sheet["spreadsheet_id"] == "abc123"
    assert c.branding == {"brand_name": "Acme Weekly", "brand_color": "#aa0000"}
    assert c.overrides == {"scoring": {"tiers": {"hot": 75}}}


def test_lists_accept_a_single_value_and_drop_duplicates(ws):
    write_client(ws, "acme", "roles: accountant\nexclude_roles: [Intern, intern, 2024]\ntiers: [HOT, hot]\n")
    c = load_client("acme")
    assert c.roles == ["accountant"] and c.exclude_roles == ["Intern", "2024"] and c.tiers == ["hot"]


def test_null_values_mean_default(ws):
    write_client(ws, "acme", "leads_per_week:\nfreshness_days:\ndedupe:\ntiers:\nemails:\ndelivery:\n")
    c = load_client("acme")
    assert c.leads_per_week == 25 and c.freshness_days == 7
    assert c.dedupe == ["company", "job", "contact"] and c.tiers == ["hot", "normal"]


def test_dedupe_may_be_switched_off(ws):
    write_client(ws, "acme", "dedupe: []\n")
    assert load_client("acme").dedupe == []


# --- validation --------------------------------------------------------------------------------

def test_error_names_the_file_the_key_and_the_fix(ws):
    msg = problem(ws, "leads_per_week: ten\n")
    assert msg == "clients/acme.yaml: leads_per_week must be a whole number > 0 (got 'ten')"


def test_every_problem_is_listed(ws):
    msg = problem(ws, "leads_per_week: ten\nfreshness_days: -1\nallow_undated: sometimes\n")
    assert msg.startswith("clients/acme.yaml has 3 problems:")
    assert "leads_per_week must be a whole number > 0 (got 'ten')" in msg
    assert "freshness_days must be a whole number > 0 (got -1)" in msg
    assert "allow_undated must be true or false (got 'sometimes')" in msg


def test_unknown_top_level_key_suggests_the_right_one(ws):
    msg = problem(ws, "leads_per_weak: 10\n")
    assert "unknown setting 'leads_per_weak' - did you mean 'leads_per_week'?" in msg
    msg = problem(ws, "colour_scheme: red\n")
    assert "unknown setting 'colour_scheme'" in msg and "clients/_template.yaml" in msg


@pytest.mark.parametrize("text, expected", [
    ("leads_per_week: 0", "leads_per_week must be a whole number > 0 (got 0)"),
    ("leads_per_week: true", "leads_per_week must be a whole number > 0 (got True)"),
    ("freshness_days: 2.5", "freshness_days must be a whole number > 0 (got 2.5)"),
    ("allow_undated: 'no'", "allow_undated must be true or false (got 'no')"),
    ("drop_reposts: 1", "drop_reposts must be true or false (got 1)"),
    ("redelivery_days: 0", "redelivery_days must be empty (= never deliver the same thing twice)"),
    ("redelivery_days: soon", "redelivery_days must be empty"),
    ("dedupe: [company, jobs]", "dedupe: 'jobs' is not allowed - did you mean 'job'?"),
    ("tiers: [hot, warm]", "tiers: 'warm' is not allowed"),
    ("tiers: []", "tiers is empty"),
    ("delivery: {formats: [excel]}", "delivery.formats: 'excel' is not allowed - did you mean 'xlsx'?"),
    ("delivery: {formats: []}", "delivery.formats is empty"),
    ("delivery: {folder: 'out/{customer}'}", "delivery.folder: unknown placeholder {customer}"),
    ("delivery: {folder: 'out/{date'}", "delivery.folder: 'out/{date' has unbalanced braces"),
    ("delivery: {google_sheet: {sheet_id: x}}", "did you mean 'delivery.google_sheet.spreadsheet_id'?"),
    ("delivery: {google_sheet: {worksheet: '{week}'}}", "delivery.google_sheet.worksheet: unknown placeholder"),
    ("delivery: {output: x}", "unknown setting 'delivery.output'"),
    ("company_size: {min: 500, max: 50}", "company_size.min (500) must not be bigger than company_size.max (50)"),
    ("company_size: {min: twenty}", "company_size.min must be a whole number >= 0 or empty (got 'twenty')"),
    ("company_size: {minimum: 5}", "did you mean 'company_size.min'?"),
    ("company_size: 50", "company_size must be a group of settings"),
    ("roles: {accountant: 1}", "roles must be a list"),
    ("roles: [accountant, '']", "roles: item 2 is empty"),
    ("roles: [accountant, [a, b]]", "roles: item 2 must be text"),
    ("exclusions: {domains: ['not a domain']}", "exclusions.domains: 'not a domain' is not a website domain"),
    ("exclusions: {company: [x]}", "did you mean 'exclusions.companies'?"),
    ("emails: {include_unverified: maybe}", "emails.include_unverified must be true or false"),
    ("emails: {verified_only: true}", "unknown setting 'emails.verified_only'"),
    ("opening_line: {max_cost_usd: -1}", "opening_line.max_cost_usd must be an amount in US dollars >= 0"),
    # NaN / infinity would silently switch the AI cost cap off
    ("opening_line: {max_cost_usd: .nan}", "opening_line.max_cost_usd must be an amount in US dollars >= 0"),
    ("opening_line: {max_cost_usd: .inf}", "opening_line.max_cost_usd must be an amount in US dollars >= 0"),
    ("opening_line: {max_cost_usd: -.inf}", "opening_line.max_cost_usd must be an amount in US dollars >= 0"),
    ("opening_line: {max_cost_usd: 1%s}" % ("0" * 400), "opening_line.max_cost_usd must be an amount in US dollars"),
    ("opening_line: {enabled: yes please}", "opening_line.enabled must be true or false"),
    ("budget: {max_paid_lookups: -5}", "budget.max_paid_lookups must be a whole number >= 0"),
    ("branding: {brand_colour: '#ffffff'}", "did you mean 'branding.brand_color'?"),
    ("branding: {brand_color: blue}", "branding.brand_color must be a hex colour like #1f4e79 (got 'blue')"),
    ("overrides: {mode: outbound}", "overrides.mode can't be set: client deliveries always run in delivery mode"),
    ("overrides: {name: other}", "overrides.name can't be set"),
    ("overrides: {scorng: {}}", "did you mean 'overrides.scoring'?"),
    ("overrides: {scoring: 5}", "overrides.scoring must be a group of settings"),
    ("overrides: {sources: {type: csv}}", "overrides.sources must be a list"),
    ("overrides: [a]", "overrides must be a group of settings"),
    ("playbook: ''", "playbook is empty"),
    ("display_name: [a, b]", "display_name must be text"),
    ("contact: sam@example.com", "contact must be a group of settings"),
])
def test_bad_values_are_explained(ws, text, expected):
    msg = problem(ws, text + "\n")
    assert msg.startswith("clients/acme.yaml")
    assert expected in msg


def test_broken_yaml_and_wrong_shape(ws):
    assert "is not valid YAML" in problem(ws, "roles: [accountant\n")
    assert "must be a list of 'key: value' settings" in problem(ws, "- a\n- b\n")
    assert "must be a list of 'key: value' settings" in problem(ws, "just some text\n")


def test_client_not_found_suggests_names(ws):
    write_client(ws, "acme", "")
    write_client(ws, "beta", "")
    with pytest.raises(ClientError) as exc:
        load_client("acmee")
    msg = str(exc.value)
    assert "client 'acmee' not found" in msg and "clients/acmee.yaml" in msg
    assert "Did you mean 'acme'?" in msg and "acme, beta" in msg
    assert "leadgen clients new acmee" in msg


def test_client_not_found_in_empty_folder(ws):
    with pytest.raises(ClientError, match="no client files in clients/ yet"):
        load_client("nobody")
    with pytest.raises(ClientError, match="no client given"):
        load_client("  ")


def test_list_clients_skips_template_hidden_and_other_files(ws):
    folder = ws / "clients"
    for n in ("zeta.yaml", "acme.yaml", "beta.yml", "_template.yaml", ".hidden.yaml", "notes.txt"):
        (folder / n).write_text("", encoding="utf-8")
    (folder / "sub.yaml").mkdir()
    assert list_clients() == ["acme", "beta", "zeta"]
    assert list_clients(str(ws / "missing")) == []


# --- Client object --------------------------------------------------------------------------------

def test_hand_built_client_accepts_plain_dicts():
    c = Client("acme", opening_line={"enabled": True, "ai": True}, emails={"include_unverified": False},
               delivery={"formats": ["csv"]}, exclusions={"companies": ["X Corp"]},
               company_size={"min": 10}, budget={"max_paid_lookups": 5})
    assert isinstance(c.opening_line, OpeningLine) and c.opening_line.ai and c.opening_line.max_cost_usd == 0.5
    assert isinstance(c.emails, EmailPolicy) and not c.email_policy_include_unverified
    assert isinstance(c.delivery, DeliverySettings) and c.delivery.formats == ["csv"]
    assert c.delivery.folder == "deliveries/{client}/{date}"
    assert isinstance(c.exclusions, Exclusions) and c.excluded_companies == ["X Corp"]
    assert isinstance(c.company_size, SizeRange) and c.company_size.max is None
    assert isinstance(c.budget, Budget) and c.budget["max_paid_lookups"] == 5
    assert c.display_name == "acme" and c.where == "client 'acme'"
    with pytest.raises(TypeError):
        Client("acme", emails=["nope"])


def test_sections_answer_mapping_style_access():
    ol = OpeningLine(enabled=True)
    assert ol["enabled"] is True and ol.get("ai") is False and ol.get("missing", 7) == 7
    assert "max_cost_usd" in ol and "other" not in ol
    assert ol.keys() == ["enabled", "ai", "max_cost_usd"]
    assert ol.to_dict() == {"enabled": True, "ai": False, "max_cost_usd": 0.5}
    assert ol == {"enabled": True, "ai": False, "max_cost_usd": 0.5}
    assert ol != OpeningLine() and ol == OpeningLine(enabled=True)
    with pytest.raises(KeyError):
        ol["nope"]
    # a real read-only mapping: code that checks isinstance(..., Mapping) works too
    from collections.abc import Mapping
    assert isinstance(ol, Mapping) and isinstance(Client("x").delivery, Mapping)
    assert dict(ol) == {**ol} == ol.to_dict() and list(ol) == ol.keys() and len(ol) == 3
    assert ol.items() == [("enabled", True), ("ai", False), ("max_cost_usd", 0.5)]
    assert ol.values() == [True, False, 0.5]
    # to_dict() is a copy: changing it never changes the client
    d = Client("x").delivery.to_dict()
    d["formats"].append("pdf")
    assert Client("x").delivery.formats == ["csv", "xlsx", "html"]


def test_delivery_folder_and_worksheet_name():
    c = Client("acme", delivery={"folder": "out/{client}/{date}",
                                 "google_sheet": {"spreadsheet_id": "x", "worksheet": "{client} {date}"}})
    day = date(2026, 9, 24)
    assert c.delivery_folder(day) == Path("out/acme/2026-09-24")
    assert c.worksheet_name(day) == "acme 2026-09-24"
    bad = Client("acme", delivery={"folder": "out/{week}"})
    with pytest.raises(ClientError, match="unknown placeholder"):
        bad.delivery_folder(day)


# --- client_playbook ----------------------------------------------------------------------------------

def test_client_playbook_applies_the_mapping(ws):
    write_client(ws, "acme", FULL_CLIENT)
    pb = client_playbook(load_client("acme"), env={"SENDER_NAME": "Pat"})
    assert pb.mode == "delivery" and not is_outbound(pb)
    assert pb.name == "client-acme"
    assert "Acme Staffing Ltd" in pb.description
    # signals
    assert pb.signals["match_keywords"] == ["accountant", "controller", "financial analyst"]
    assert pb.signals["exclude_keywords"] == ["intern", "Volunteer", "trainee"]   # added, no duplicate
    assert pb.signals["max_age_days"] == 10
    assert pb.signals["allow_undated"] is True and pb.signals["drop_reposts"] is False
    # buyers
    assert pb.buyers["titles"] == ["CFO", "VP Finance", "Controller", "Head of Talent"]
    assert pb.buyers["max_contacts_per_company"] == 1
    # icp
    assert pb.icp["locations"] == ["Texas", "Oklahoma"] and pb.icp["exclude_locations"] == ["Dallas"]
    assert pb.icp["employees"] == {"min": 20, "max": 1000}
    assert pb.icp["industries"] == ["software"]
    assert pb.icp["exclude_domains"] == ["competitor.example", "bigclient.com"]
    assert pb.icp["exclude_keywords"] == ["staffing agency", "staffing", "recruiting"]
    # the company names are NOT regexes and do not go into the playbook
    assert pb.icp["exclude_company_patterns"] == []
    # volume / cost
    assert pb.enrichment["max_companies"] == 60
    assert pb.usage["max_paid_lookups"] == 150
    # hand-over exporters removed, review exporters kept
    assert [e["type"] for e in pb.outbound["exporters"]] == ["csv", "json"]
    # branding over the base's delivery section
    assert pb.delivery["brand_name"] == "Acme Weekly" and pb.delivery["brand_color"] == "#aa0000"
    assert pb.delivery["footer"] == "base footer"
    # overrides deep-merged last
    assert pb.scoring["tiers"] == {"hot": 75, "normal": 60}
    assert pb.scoring["weights"]["intent"] == 40
    # base settings that the client does not touch survive (AI opening lines use them)
    assert pb.writer["provider"] == "anthropic" and pb.writer["model"] == "claude-haiku-4-5"
    assert pb.offer["sender_name"] == "Pat"      # ${ENV} expanded with the given env
    assert pb.sources[0]["type"] == "csv"


def test_client_playbook_keeps_base_lists_when_client_leaves_them_empty(ws):
    write_client(ws, "acme", "playbook: playbooks/base.yaml\n")
    pb = client_playbook(load_client("acme"), env={})
    assert pb.signals["match_keywords"] == ["nurse", "midwife"]
    assert pb.buyers["titles"] == ["Head of Nursing"]
    assert pb.signals["exclude_keywords"] == ["intern", "Volunteer"]
    # targeting lists always come from the client file ([] = anywhere / any size)
    assert pb.icp["locations"] == [] and pb.icp["employees"] == {"min": None, "max": None}
    # budget 0 = no extra cap: the base playbook's cap stays
    assert pb.usage["max_paid_lookups"] == 40
    assert pb.enrichment["max_companies"] == 50   # 25 leads/week * 2
    assert pb.offer["sender_name"] == "Nobody"    # env default


def test_base_playbook_path_resolution_and_relative_sources(ws):
    # relative to the current folder
    write_client(ws, "acme", "playbook: playbooks/base.yaml\n")
    pb = client_playbook(load_client("acme"), env={})
    assert pb.path == Path("playbooks/base.yaml")
    # sources' relative paths still resolve against the BASE playbook's folder
    assert pb.resolve_path("data/jobs.csv") == Path("playbooks/data/jobs.csv")
    assert pb.resolve_path("data/jobs.csv").is_file()

    # relative to the client file's own folder
    (ws / "clients" / "base-here.yaml").write_text(BASE_PLAYBOOK.replace("name: base-delivery", "name: b2"),
                                                   encoding="utf-8")
    write_client(ws, "beta", "playbook: base-here.yaml\n")
    pb = client_playbook(load_client("beta"), env={})
    assert pb.path == Path("clients/base-here.yaml")

    # relative to the project root when run from another folder
    sub = ws / "somewhere"
    sub.mkdir()
    import os
    os.chdir(sub)
    try:
        c = load_client(str(ws / "clients" / "acme.yaml"))
        pb = client_playbook(c, env={})
        assert pb.path.resolve() == (ws / "playbooks" / "base.yaml").resolve()
        assert pb.resolve_path("data/jobs.csv").resolve() == (ws / "playbooks" / "data" / "jobs.csv").resolve()
    finally:
        os.chdir(ws)


def test_missing_base_playbook_is_explained(ws):
    write_client(ws, "acme", "playbook: playbooks/nope.yaml\n")
    with pytest.raises(ClientError) as exc:
        client_playbook(load_client("acme"))
    msg = str(exc.value)
    assert msg.startswith("clients/acme.yaml: base playbook 'playbooks/nope.yaml' not found")
    assert "clients/playbooks/nope.yaml" in msg and "'playbook:' line" in msg


def test_invalid_base_playbook_is_explained(ws):
    (ws / "playbooks" / "broken.yaml").write_text("name: x\nscoring: {tiers: {hot: 10, normal: 50}}\n",
                                                  encoding="utf-8")
    write_client(ws, "acme", "playbook: playbooks/broken.yaml\n")
    with pytest.raises(ClientError, match=r"(?s)base playbook playbooks/broken.yaml can.t be used.*scoring.tiers"):
        client_playbook(load_client("acme"))


def test_invalid_result_is_explained(ws):
    write_client(ws, "acme", "playbook: playbooks/base.yaml\noverrides: {scoring: {tiers: {hot: 200}}}\n")
    with pytest.raises(ClientError) as exc:
        client_playbook(load_client("acme"), env={})
    msg = str(exc.value)
    assert msg.startswith("clients/acme.yaml: the settings built for this client (base playbook "
                          "playbooks/base.yaml + the client file) are not valid:\n  - scoring.tiers")
    assert "invalid playbook" not in msg     # the base file itself is fine: don't blame it


@pytest.mark.parametrize("base_line, leads, overrides, expected", [
    ("", 25, "", 50),                          # base default (200) does not count as "set"
    ("", 3, "", 10),                           # never fewer than 10
    ("", 150, "", 300),                        # higher than the default 200 is fine
    ("  max_companies: 20\n", 25, "", 20),     # the base set a lower number: kept
    ("  max_companies: 500\n", 25, "", 50),    # a higher base number is capped
    ("  max_companies: 0\n", 25, "", 50),      # 0 (= no cap) in the base: use the target
    ("", 25, "overrides: {enrichment: {max_companies: 7}}\n", 7),
    ("", 25, "overrides: {enrichment: {max_companies: 400}}\n", 400),   # overrides always win
])
def test_max_companies_rule(ws, base_line, leads, overrides, expected):
    text = BASE_PLAYBOOK.replace("  verifier: {type: basic}\n", "  verifier: {type: basic}\n" + base_line)
    (ws / "playbooks" / "base.yaml").write_text(text, encoding="utf-8")
    write_client(ws, "acme", f"playbook: playbooks/base.yaml\nleads_per_week: {leads}\n{overrides}")
    assert client_playbook(load_client("acme"), env={}).enrichment["max_companies"] == expected


def test_hand_over_exporters_are_removed_even_from_overrides(ws):
    write_client(ws, "acme", """\
        playbook: playbooks/base.yaml
        overrides:
          outbound:
            exporters: [{type: instantly}, {type: smartlead_csv}, {type: csv, label: review},
                        {type: my_future_exporter}]
    """)
    pb = client_playbook(load_client("acme"), env={})
    # unknown types are kept: the pipeline reports them when it builds them
    assert pb.outbound["exporters"] == [{"type": "csv", "label": "review"}, {"type": "my_future_exporter"}]
    assert is_handover_exporter("instantly") and is_handover_exporter("webhook")
    assert not is_handover_exporter("csv") and not is_handover_exporter("gsheets")
    assert not is_handover_exporter("does-not-exist")


def test_mode_is_forced_even_for_hand_built_clients_with_overrides(ws):
    c = Client("Acme Staffing & Co", playbook="playbooks/base.yaml",
               overrides={"mode": "outbound", "name": "evil", "buyers": {"max_contacts_per_company": 3}},
               company_size={"min": 1, "max": 9}, exclusions={"domains": ["bigclient.com"]})
    pb = client_playbook(c, env={})
    assert pb.mode == "delivery" and pb.name == "client-acme-staffing-co"
    assert pb.buyers["max_contacts_per_company"] == 3     # overrides may change it
    assert pb.icp["employees"] == {"min": 1, "max": 9}
    assert "bigclient.com" in pb.icp["exclude_domains"]


def test_playbook_name_is_sanitised():
    assert playbook_name_for("acme") == "client-acme"
    assert playbook_name_for("Acme Staffing Ltd") == "client-acme-staffing-ltd"
    assert playbook_name_for("über/../x") == "client-ber-..-x"
    assert playbook_name_for("   ") == "client"


def test_client_playbook_with_a_base_that_sets_nothing(ws):
    (ws / "playbooks" / "tiny.yaml").write_text("name: tiny\n", encoding="utf-8")
    c = Client("acme", playbook="playbooks/tiny.yaml", roles=["welder"])
    pb = client_playbook(c, env={})
    assert pb.signals["match_keywords"] == ["welder"]
    assert pb.outbound["exporters"] == [{"type": "csv"}]     # the default review export
    assert pb.signals["exclude_keywords"] == []
    assert pb.icp["exclude_domains"] == []


# --- the template + new client files ----------------------------------------------------------------------

def test_template_has_every_key_with_the_documented_defaults():
    data = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    assert list(data) == list(CLIENT_KEYS)
    c = load_client(str(TEMPLATE))
    assert c.contact == {"name": "", "email": ""}
    c.contact = {}
    assert c == Client("_template", path=TEMPLATE)
    # name-agnostic: no real-looking client or company names in the active settings
    assert c.display_name == "_template" and c.roles == [] and c.exclusions.companies == []


def test_template_client_builds_a_playbook(ws):
    new = new_client_file("northwind")
    c = load_client("northwind")
    c.playbook = "playbooks/base.yaml"
    pb = client_playbook(c, env={})
    assert pb.name == "client-northwind" and pb.mode == "delivery"
    assert new == Path("clients/northwind.yaml")


def test_new_client_file_copies_the_template(ws):
    path = new_client_file("acme-staffing")
    assert path == Path("clients/acme-staffing.yaml") and path.is_file()
    assert path.read_text(encoding="utf-8") == TEMPLATE.read_text(encoding="utf-8")
    assert load_client("acme-staffing").display_name == "acme-staffing"
    assert "acme-staffing" in list_clients()


def test_new_client_file_refuses_to_overwrite(ws):
    write_client(ws, "acme", "leads_per_week: 3\n")
    with pytest.raises(ClientError, match="clients/acme.yaml already exists"):
        new_client_file("acme")
    assert load_client("acme").leads_per_week == 3
    (ws / "clients" / "beta.yml").write_text("", encoding="utf-8")
    with pytest.raises(ClientError, match="beta.yml already exists"):
        new_client_file("beta")


@pytest.mark.parametrize("bad, suggestion", [
    ("Acme Staffing", "acme-staffing"), ("_private", "private"), ("acme.yaml", "acme-yaml"), ("", "my-client"),
    ("-acme", "acme"),
])
def test_new_client_file_checks_the_name(ws, bad, suggestion):
    with pytest.raises(ClientError) as exc:
        new_client_file(bad)
    assert "is not allowed" in str(exc.value) and f"'{suggestion}'" in str(exc.value)
    assert list_clients() == []


def test_new_client_file_template_choice(ws):
    custom = ws / "my_template.yaml"
    custom.write_text("leads_per_week: 5\n", encoding="utf-8")
    path = new_client_file("small", clients_dir="agency", template=str(custom))
    assert path == Path("agency/small.yaml") and load_client("small", "agency").leads_per_week == 5
    with pytest.raises(ClientError, match="client template not found: nope.yaml"):
        new_client_file("other", template="nope.yaml")
    # a template inside the clients folder is used when the default path is missing
    (ws / "agency" / "_template.yaml").write_text("freshness_days: 3\n", encoding="utf-8")
    assert load_client(str(new_client_file("third", clients_dir="agency")), "agency").freshness_days == 3
