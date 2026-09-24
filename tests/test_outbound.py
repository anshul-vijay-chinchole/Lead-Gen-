"""Tests for the exporters / senders in ``leadgen.outbound``.

File exporters are checked on disk; API senders run against ``FakeHttp`` with
payloads shaped like each provider's documented responses; the Google Sheets
exporter runs against a fake ``gspread`` module injected into ``sys.modules``.
"""
from __future__ import annotations

import csv
import json
import logging
import re
import sys
import types
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from leadgen import registry
from leadgen.context import MissingCredentialError
from leadgen.http import HttpError
from leadgen.models import Company, Contact, Lead, Message, ScoreBreakdown, Signal
from leadgen.outbound.csv_export import (CsvExporter, JsonExporter, display_company_name,
                                         followup_subject_steps, format_body, guard_cell, lead_rows,
                                         outbound_row, sequence_keys, write_csv)
from leadgen.outbound.gsheets import GoogleSheetsError, GoogleSheetsExporter
from leadgen.outbound.instantly import InstantlyApiExporter, InstantlyCsvExporter, InstantlyError
from leadgen.outbound.smartlead import SmartleadApiExporter, SmartleadCsvExporter, SmartleadError
from leadgen.outbound.webhook import WebhookExporter, WebhookExportError

INSTANTLY_URL = "https://api.instantly.ai/api/v2/leads"
SMARTLEAD_URL = "https://server.smartlead.ai/api/v1/campaigns/4242/leads"
HOOK_URL = "https://hook.eu1.make.com/abc123SECRETtoken"


# --- builders ----------------------------------------------------------------------------

def make_lead(name: str = "Acme Inc", domain: str = "acme.com", score: int = 80, tier: str = "hot",
              email: Optional[str] = "jane@acme.com", first: str = "Jane", last: str = "Doe",
              title: str = "CFO", steps: int = 3, signals: Optional[List[Signal]] = None,
              contact: bool = True, **kw: Any) -> Lead:
    if signals is None:
        signals = [Signal(type="job_posting", title="Senior Accountant", posted_at="2026-09-20",
                          url=f"https://jobs.example.com/{domain}/1", source="csv")]
    company = Company(name=name, domain=domain, industry="Manufacturing", employees=120,
                      location="Manchester", country="UK", signals=signals, sources=["csv", "apollo"])
    ct = None
    if contact:
        ct = Contact(first_name=first, last_name=last, title=title, email=email or "",
                     email_status="valid" if email else "unknown",
                     linkedin_url=f"https://www.linkedin.com/in/{first.lower()}-{last.lower()}",
                     phone="+44 161 496 0000")
    msgs = [Message(step=1, day=1, subject=f"accountant hire at {name.split()[0]}",
                    body=f"Hi {first},\n\nSaw the Senior Accountant role.\n\nSam")]
    for i in range(2, steps + 1):
        msgs.append(Message(step=i, day=i * 3, subject="", body=f"Follow-up {i} for {first}"))
    lead = Lead(company=company, contact=ct, score=score, tier=tier, playbook="test",
                breakdown=ScoreBreakdown(reasons=["fresh job post (4d)", "size fits"]),
                messages=msgs if steps else [], personalization=f"Saw {name} is hiring.",
                hypothesis="Growing finance team.", notes=list(kw.pop("notes", [])))
    for k, v in kw.items():
        setattr(lead, k, v)
    return lead


def read_csv(path: str) -> List[Dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_header(path: str) -> List[str]:
    with open(path, newline="", encoding="utf-8") as f:
        return next(csv.reader(f))


# --- registry / class attributes --------------------------------------------------------

def test_registry_resolves_every_exporter():
    expected = {
        "csv": CsvExporter, "json": JsonExporter, "instantly_csv": InstantlyCsvExporter,
        "instantly": InstantlyApiExporter, "smartlead_csv": SmartleadCsvExporter,
        "smartlead": SmartleadApiExporter, "gsheets": GoogleSheetsExporter, "webhook": WebhookExporter,
    }
    for name, cls in expected.items():
        assert registry.resolve("exporter", name) is cls
        assert cls.name == name


def test_exporter_class_attributes():
    for cls in (CsvExporter, JsonExporter, GoogleSheetsExporter):
        assert cls.scope == "all" and cls.is_send is False
    for cls in (InstantlyCsvExporter, SmartleadCsvExporter):
        assert cls.scope == "outbound" and cls.is_send is False and cls.offline is True
    for cls in (InstantlyApiExporter, SmartleadApiExporter, WebhookExporter):
        assert cls.scope == "outbound" and cls.is_send is True and cls.offline is False
    assert CsvExporter.offline and JsonExporter.offline and not GoogleSheetsExporter.offline
    assert InstantlyApiExporter.env_key == "INSTANTLY_API_KEY"
    assert SmartleadApiExporter.env_key == "SMARTLEAD_API_KEY"
    assert WebhookExporter.env_key == "LEADGEN_EXPORT_WEBHOOK_URL"
    assert GoogleSheetsExporter.env_key == "GOOGLE_APPLICATION_CREDENTIALS"


def test_constructors_do_not_need_credentials(make_ctx):
    ctx = make_ctx()
    for t in ("instantly", "smartlead", "webhook", "gsheets", "csv", "json"):
        registry.create("exporter", {"type": t}, ctx)  # must not raise


# --- helpers ------------------------------------------------------------------------------

def test_guard_cell_prefixes_formula_starts():
    for bad in ("=1+1", "+44", "-2", "@SUM(A1)", "\tx", "\rx"):
        assert guard_cell(bad) == "'" + bad
    assert guard_cell("Acme") == "Acme" and guard_cell("") == ""
    assert guard_cell(5) == 5 and guard_cell(None) is None


def test_display_company_name():
    assert display_company_name("Acme Inc.") == "Acme"
    assert display_company_name("Acme, LLC") == "Acme"
    assert display_company_name("Acme Pty Ltd") == "Acme"
    assert display_company_name("Acme Holdings Ltd") == "Acme Holdings"
    assert display_company_name("Nestle S.A.") == "Nestle"
    assert display_company_name("Co") == "Co"  # never strips to nothing
    assert display_company_name("Visa") == "Visa"


def test_format_body_html_escapes_and_breaks():
    assert format_body("a\r\nb") == "a\nb"
    assert format_body("Hi <b>\n\nx & y", "html") == "Hi &lt;b&gt;<br><br>x &amp; y"


def test_sequence_keys_and_followup_subjects():
    assert sequence_keys(0) == ["subject_1", "email_1"]
    assert sequence_keys(3) == ["subject_1", "email_1", "email_2", "email_3"]
    assert sequence_keys(3, [3]) == ["subject_1", "email_1", "email_2", "subject_3", "email_3"]
    lead = make_lead()
    lead.messages[2].subject = "new thread"
    assert followup_subject_steps([lead, make_lead()]) == [3]


def test_outbound_row_fields():
    lead = make_lead(name="Acme Inc.", steps=4)
    lead.company.website = "www.acme.com"
    row = outbound_row(lead, 5)
    assert row["email"] == "jane@acme.com" and row["first_name"] == "Jane" and row["last_name"] == "Doe"
    assert row["company_name"] == "Acme" and row["website"] == "https://www.acme.com"
    assert row["job_title"] == "CFO" and row["location"] == "Manchester, UK"
    assert row["signal"] == "Senior Accountant" and row["signal_type"] == "job_posting"
    assert row["score"] == 80 and row["tier"] == "hot" and row["lead_id"] == lead.id
    assert row["subject_1"].startswith("accountant hire") and row["email_2"] == "Follow-up 2 for Jane"
    assert row["email_4"] == "Follow-up 4 for Jane" and row["email_5"] == ""
    assert outbound_row(lead, clean_company=False)["company_name"] == "Acme Inc."


def test_outbound_row_without_contact_or_signal():
    lead = make_lead(contact=False, signals=[], steps=0)
    lead.company.domain = ""
    row = outbound_row(lead, 1)
    assert row["email"] == "" and row["first_name"] == "" and row["signal"] == ""
    assert row["website"] == "" and row["subject_1"] == "" and row["email_1"] == ""


def test_outbound_row_orders_messages_by_step():
    lead = make_lead(steps=0)
    lead.messages = [Message(step=2, day=3, subject="", body="second"),
                     Message(step=1, day=1, subject="first subj", body="first")]
    row = outbound_row(lead)
    assert row["subject_1"] == "first subj" and row["email_1"] == "first" and row["email_2"] == "second"


def test_write_csv_phone_exemption(tmp_path):
    p = tmp_path / "x.csv"
    write_csv(p, ["phone", "name"], [["+44 20 7946 0958", "+cmd"], ["=cmd|' /C calc'!A0", "ok"]],
              phone_columns=["phone"])
    rows = list(csv.reader(open(p, encoding="utf-8")))
    assert rows[1] == ["+44 20 7946 0958", "'+cmd"]
    assert rows[2][0].startswith("'=cmd")


# --- CsvExporter ---------------------------------------------------------------------------

def test_csv_exporter_writes_review_sheet(make_ctx, tmp_path):
    ctx = make_ctx()
    a = make_lead(name="Low Co", domain="low.com", score=55, tier="skip", steps=0, notes=["no email found"])
    b = make_lead(name="High Inc", domain="high.com", score=91, steps=4)
    c = make_lead(name="Mid Ltd", domain="mid.com", score=70, tier="normal", steps=2)
    res = CsvExporter({"type": "csv"}, ctx).export([a, b, c], tmp_path)
    assert res.exporter == "csv" and res.count == 3
    assert res.path == str(tmp_path / "opportunities.csv")
    assert res.exported_ids == [b.id, c.id, a.id]
    header = read_header(res.path)
    assert header == [
        "lead_id", "score", "tier", "stage", "company", "domain", "industry", "employees", "location",
        "top_signal_type", "top_signal", "signal_age_days", "signal_url", "signal_count", "contact_name",
        "contact_title", "email", "email_status", "linkedin", "phone", "personalization", "hypothesis",
        "subject_1", "email_1", "followup_1", "followup_2", "followup_3", "score_reasons", "notes", "sources",
    ]
    rows = read_csv(res.path)
    assert [r["company"] for r in rows] == ["High Inc", "Mid Ltd", "Low Co"]
    top = rows[0]
    assert top["lead_id"] == b.id and top["score"] == "91" and top["tier"] == "hot"
    assert top["signal_age_days"] == "4"  # 2026-09-20 vs TODAY 2026-09-24
    assert top["signal_count"] == "1" and top["employees"] == "120"
    assert top["location"] == "Manchester, UK" and top["email_status"] == "valid"
    assert top["email_1"] == "Hi Jane,\n\nSaw the Senior Accountant role.\n\nSam"
    assert top["followup_3"] == "Follow-up 4 for Jane"
    assert top["score_reasons"] == "fresh job post (4d); size fits"
    assert top["sources"] == "csv, apollo"
    assert rows[0]["phone"] == "'+44 161 496 0000"  # review sheet guards everything
    assert rows[1]["followup_2"] == "" and rows[2]["subject_1"] == ""
    assert rows[2]["notes"] == "no email found"


def test_csv_exporter_formula_injection_and_unicode(make_ctx, tmp_path):
    ctx = make_ctx()
    lead = make_lead(name="=HYPERLINK(\"http://evil\",\"x\")", domain="evil.com",
                     signals=[Signal(type="job_posting", title="@Buchhalter (m/w/d) Zürich")])
    lead.contact.title = "-Directeur Général"
    lead.messages[2].subject = "new thread here"
    res = CsvExporter({}, ctx).export([lead, make_lead(name="Müller GmbH", domain="mueller.de")], tmp_path)
    rows = read_csv(res.path)
    evil = next(r for r in rows if r["domain"] == "evil.com")
    assert evil["company"].startswith("'=HYPERLINK")
    assert evil["top_signal"] == "'@Buchhalter (m/w/d) Zürich"
    assert evil["contact_title"] == "'-Directeur Général"
    assert evil["signal_age_days"] == ""  # undated signal
    assert evil["followup_2"] == "Subject: new thread here\n\nFollow-up 3 for Jane"
    assert any(r["company"] == "Müller GmbH" for r in rows)
    raw = (tmp_path / "opportunities.csv").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")  # plain UTF-8, no BOM
    assert "Müller".encode("utf-8") in raw


def test_csv_exporter_options(make_ctx, tmp_path):
    ctx = make_ctx()
    lead = make_lead(name="=SUM(1)")
    res = CsvExporter({"filename": "sheets/review.csv", "formula_guard": False, "label": "review"},
                      ctx).export([lead], tmp_path)
    assert res.path == str(tmp_path / "sheets" / "review.csv") and res.exporter == "review"
    assert read_csv(res.path)[0]["company"] == "=SUM(1)"


def test_csv_exporter_empty_and_bare_leads(make_ctx, tmp_path):
    ctx = make_ctx()
    res = CsvExporter({}, ctx).export([], tmp_path)
    assert res.count == 0 and res.exported_ids == []
    header = read_header(res.path)
    assert "followup_1" not in header and header[-3:] == ["score_reasons", "notes", "sources"]
    bare = Lead(company=Company(name="Bare"))
    res = CsvExporter({}, ctx).export([bare], tmp_path)
    row = read_csv(res.path)[0]
    assert row["company"] == "Bare" and row["contact_name"] == "" and row["top_signal"] == ""
    assert row["employees"] == "" and row["signal_count"] == "0" and row["score"] == "0"


def test_lead_rows_native_types():
    header, rows = lead_rows([make_lead()], today=None)
    row = dict(zip(header, rows[0]))
    assert isinstance(row["score"], int) and isinstance(row["employees"], int)
    assert isinstance(row["signal_count"], int) and isinstance(row["signal_age_days"], int)


# --- JsonExporter -------------------------------------------------------------------------

def test_json_exporter(make_ctx, tmp_path):
    ctx = make_ctx()
    a, b = make_lead(name="Zürich AG", domain="zurich.ch", score=60), make_lead(score=95)
    res = JsonExporter({"type": "json"}, ctx).export([a, b], tmp_path)
    assert res.exporter == "json" and res.count == 2 and res.path.endswith("leads.json")
    assert res.exported_ids == [b.id, a.id]
    text = (tmp_path / "leads.json").read_text(encoding="utf-8")
    assert "Zürich AG" in text and "\n  " in text  # pretty + non-ASCII kept
    data = json.loads(text)
    assert [d["id"] for d in data] == [b.id, a.id]
    back = Lead.from_dict(data[1])
    assert back.id == a.id and back.company.signals[0].title == "Senior Accountant"


def test_json_exporter_filename_and_empty(make_ctx, tmp_path):
    res = JsonExporter({"filename": "out/all.json"}, make_ctx()).export([], tmp_path)
    assert res.count == 0 and json.loads((tmp_path / "out" / "all.json").read_text()) == []


# --- Instantly CSV -------------------------------------------------------------------------

def test_instantly_csv(make_ctx, tmp_path, caplog):
    ctx = make_ctx()
    good = make_lead(name="Acme Inc", score=90, steps=4)
    other = make_lead(name="Beta Ltd", domain="beta.io", score=70, first="Bob", last="Ray", steps=2)
    noemail = make_lead(name="Gamma", domain="gamma.io", email=None)
    with caplog.at_level(logging.WARNING):
        res = InstantlyCsvExporter({"type": "instantly_csv"}, ctx).export([other, noemail, good], tmp_path)
    assert res.exporter == "instantly_csv" and res.count == 2
    assert res.path == str(tmp_path / "instantly_upload.csv")
    assert res.exported_ids == [good.id, other.id]
    assert "1 skipped" in res.detail and "without an email" in caplog.text
    assert read_header(res.path) == [
        "email", "first_name", "last_name", "company_name", "website", "personalization", "job_title",
        "linkedin_url", "phone", "location", "subject_1", "email_1", "email_2", "email_3", "email_4",
        "signal", "score", "tier",
    ]
    rows = read_csv(res.path)
    assert rows[0]["email"] == "jane@acme.com" and rows[0]["company_name"] == "Acme"
    assert rows[0]["website"] == "https://acme.com" and rows[0]["phone"] == "+44 161 496 0000"
    assert rows[0]["email_4"] == "Follow-up 4 for Jane" and rows[0]["score"] == "90"
    assert rows[1]["first_name"] == "Bob" and rows[1]["email_3"] == "" and rows[1]["tier"] == "hot"
    assert rows[0]["personalization"] == "Saw Acme Inc is hiring."


def test_instantly_csv_new_thread_subject_and_html(make_ctx, tmp_path):
    ctx = make_ctx()
    lead = make_lead()
    lead.messages[2].subject = "different angle"
    res = InstantlyCsvExporter({"body_format": "html", "clean_company_name": False}, ctx).export([lead], tmp_path)
    header = read_header(res.path)
    assert header[10:15] == ["subject_1", "email_1", "email_2", "subject_3", "email_3"]
    row = read_csv(res.path)[0]
    assert row["subject_3"] == "different angle"
    assert row["email_1"] == "Hi Jane,<br><br>Saw the Senior Accountant role.<br><br>Sam"
    assert row["company_name"] == "Acme Inc"


def test_instantly_csv_rejects_bad_body_format(make_ctx, tmp_path):
    with pytest.raises(ValueError, match="body_format"):
        InstantlyCsvExporter({"body_format": "markdown"}, make_ctx()).export([make_lead()], tmp_path)


def test_instantly_csv_without_messages_keeps_columns(make_ctx, tmp_path):
    res = InstantlyCsvExporter({}, make_ctx()).export([make_lead(steps=0)], tmp_path)
    assert read_header(res.path)[10:] == ["subject_1", "email_1", "signal", "score", "tier"]


# --- Instantly API --------------------------------------------------------------------------

def instantly_ok(call: Dict[str, Any]) -> Dict[str, Any]:
    body = call["json"]
    return {"id": "0199a1b2-c3d4-7e5f-8a9b-" + body["email"].split("@")[0].ljust(12, "0")[:12],
            "timestamp_created": "2026-09-24T10:00:00.000Z", "organization": "org-1",
            "campaign": body["campaign"], "status": 1, "email": body["email"],
            "first_name": body.get("first_name"), "payload": body.get("custom_variables")}


def test_instantly_api_happy_path(make_ctx, tmp_path):
    ctx = make_ctx(env={"INSTANTLY_API_KEY": "inst-key-123"})
    ctx.http.add("POST", INSTANTLY_URL, fn=instantly_ok)
    a, b = make_lead(score=70, steps=4), make_lead(name="Beta Ltd", domain="beta.io", first="Bob", score=90)
    exp = InstantlyApiExporter({"type": "instantly", "campaign_id": "camp-uuid-1"}, ctx)
    res = exp.export([a, b], tmp_path)
    assert res.exporter == "instantly" and res.count == 2
    assert res.exported_ids == [b.id, a.id]
    assert "added 2/2 to campaign camp-uuid-1" in res.detail
    assert len(ctx.http.calls) == 2
    call = ctx.http.calls[1]
    assert call["method"] == "POST" and call["url"] == INSTANTLY_URL
    assert call["headers"]["Authorization"] == "Bearer inst-key-123"
    body = call["json"]
    assert body["campaign"] == "camp-uuid-1" and body["email"] == "jane@acme.com"
    assert body["first_name"] == "Jane" and body["last_name"] == "Doe" and body["company_name"] == "Acme"
    assert body["website"] == "https://acme.com" and body["phone"] == "+44 161 496 0000"
    assert body["personalization"] == "Saw Acme Inc is hiring."
    assert body["skip_if_in_workspace"] is True and body["skip_if_in_campaign"] is True
    cv = body["custom_variables"]
    assert list(cv)[:5] == ["subject_1", "email_1", "email_2", "email_3", "email_4"]
    assert cv["email_4"] == "Follow-up 4 for Jane" and cv["score"] == 70 and cv["tier"] == "hot"
    assert cv["job_title"] == "CFO" and cv["linkedin_url"].startswith("https://www.linkedin.com/in/")
    assert cv["signal"] == "Senior Accountant" and cv["location"] == "Manchester, UK"
    assert all(v is None or isinstance(v, (str, int, float, bool)) for v in cv.values())
    # first call is for the higher-scored lead; its missing step 4 is an empty variable
    assert ctx.http.calls[0]["json"]["custom_variables"]["email_4"] == ""


def test_instantly_api_config_overrides(make_ctx, tmp_path):
    ctx = make_ctx(env={"MY_INST": "k2"})
    ctx.http.add("POST", "https://proxy.example.com/v2/leads", fn=instantly_ok)
    lead = make_lead()
    lead.contact.phone = ""
    exp = InstantlyApiExporter({"campaign_id": "c1", "api_key_env": "MY_INST", "skip_if_in_workspace": False,
                                "skip_if_in_campaign": "false", "base_url": "https://proxy.example.com/",
                                "leads_path": "v2/leads", "label": "instantly-eu",
                                "extra_fields": {"verify_leads_on_import": True, "campaign": "hijack",
                                                 "email": "x@y.z"}}, ctx)
    res = exp.export([lead], tmp_path)
    assert res.count == 1 and res.exporter == "instantly-eu"
    body = ctx.http.calls[0]["json"]
    assert ctx.http.calls[0]["headers"]["Authorization"] == "Bearer k2"
    assert body["skip_if_in_workspace"] is False and body["skip_if_in_campaign"] is False
    assert body["verify_leads_on_import"] is True
    assert body["campaign"] == "c1" and body["email"] == "jane@acme.com"  # cannot be overridden
    assert "phone" not in body  # empty optional fields omitted


def test_instantly_api_requires_campaign(make_ctx, tmp_path):
    ctx = make_ctx(env={"INSTANTLY_API_KEY": "k"})
    with pytest.raises(ValueError, match="campaign_id"):
        InstantlyApiExporter({}, ctx).export([make_lead()], tmp_path)
    assert ctx.http.calls == []


def test_instantly_api_missing_key(make_ctx, tmp_path):
    ctx = make_ctx()
    with pytest.raises(MissingCredentialError, match="INSTANTLY_API_KEY"):
        InstantlyApiExporter({"campaign_id": "c"}, ctx).export([make_lead()], tmp_path)
    assert ctx.http.calls == []


def test_instantly_api_dry_run(make_ctx, tmp_path):
    ctx = make_ctx(dry_run=True)  # no key, no campaign: still fine in dry-run
    res = InstantlyApiExporter({}, ctx).export([make_lead()], tmp_path)
    assert res.count == 0 and res.detail == "dry-run" and res.exported_ids == []
    assert ctx.http.calls == []


def test_instantly_api_no_leads_makes_no_call(make_ctx, tmp_path):
    ctx = make_ctx()
    res = InstantlyApiExporter({"campaign_id": "c"}, ctx).export([], tmp_path)
    assert res.count == 0 and ctx.http.calls == []


def test_instantly_api_per_lead_failure_not_fatal(make_ctx, tmp_path, caplog):
    ctx = make_ctx(env={"INSTANTLY_API_KEY": "k"})

    def route(call):
        if call["json"]["email"] == "bob@beta.io":
            return 400, {"statusCode": 400, "error": "Bad Request", "message": "body/email must match format \"email\""}
        return instantly_ok(call)

    ctx.http.add("POST", INSTANTLY_URL, fn=route)
    good = make_lead(score=90)
    bad = make_lead(name="Beta", domain="beta.io", first="Bob", email="bob@beta.io", score=80)
    noemail = make_lead(name="Gamma", domain="gamma.io", email=None, score=70)
    with caplog.at_level(logging.WARNING):
        res = InstantlyApiExporter({"campaign_id": "c"}, ctx).export([bad, good, noemail], tmp_path)
    assert res.count == 1 and res.exported_ids == [good.id]
    assert "added 1/3" in res.detail and "1 failed" in res.detail and "1 skipped (no email)" in res.detail
    assert "bob@beta.io" in caplog.text and "must match format" in caplog.text
    assert len(ctx.http.calls) == 2  # the no-email lead never hits the API


def test_instantly_api_unauthorized_raises(make_ctx, tmp_path):
    ctx = make_ctx(env={"INSTANTLY_API_KEY": "bad"})
    ctx.http.add("POST", INSTANTLY_URL, status=401,
                 json={"statusCode": 401, "error": "Unauthorized", "message": "Invalid API key"})
    leads = [make_lead(domain=f"c{i}.com", email=f"x@c{i}.com") for i in range(3)]
    with pytest.raises(InstantlyError, match="Invalid API key") as ei:
        InstantlyApiExporter({"campaign_id": "c"}, ctx).export(leads, tmp_path)
    assert "INSTANTLY_API_KEY" in str(ei.value) and "2 lead(s) not attempted" in str(ei.value)
    assert len(ctx.http.calls) == 1


def test_instantly_api_fatal_after_success_returns_partial(make_ctx, tmp_path):
    ctx = make_ctx(env={"INSTANTLY_API_KEY": "k"})
    ctx.http.add("POST", INSTANTLY_URL, fn=instantly_ok, times=1)
    ctx.http.add("POST", INSTANTLY_URL, status=404, json={"message": "Campaign not found"})
    leads = [make_lead(domain=f"c{i}.com", email=f"x@c{i}.com", score=90 - i) for i in range(4)]
    res = InstantlyApiExporter({"campaign_id": "c"}, ctx).export(leads, tmp_path)
    assert res.exported_ids == [leads[0].id] and res.count == 1
    assert "stopped" in res.detail and "Campaign not found" in res.detail
    assert len(ctx.http.calls) == 2


def test_instantly_api_network_error_and_consecutive_limit(make_ctx, tmp_path):
    ctx = make_ctx(env={"INSTANTLY_API_KEY": "k"})

    def boom(call):
        raise HttpError(0, call["url"], "ConnectionError: connection reset")

    ctx.http.add("POST", INSTANTLY_URL, fn=boom)
    leads = [make_lead(domain=f"c{i}.com", email=f"x@c{i}.com") for i in range(5)]
    with pytest.raises(InstantlyError, match="2 failures in a row"):
        InstantlyApiExporter({"campaign_id": "c", "max_consecutive_failures": 2}, ctx).export(leads, tmp_path)
    assert len(ctx.http.calls) == 2


def test_instantly_api_server_error_counts_as_failure(make_ctx, tmp_path):
    ctx = make_ctx(env={"INSTANTLY_API_KEY": "k"})
    ctx.http.add("POST", INSTANTLY_URL, status=500, text="<html>Internal error</html>", times=1)
    ctx.http.add("POST", INSTANTLY_URL, fn=instantly_ok)
    a, b = make_lead(score=90), make_lead(domain="b.com", email="b@b.com", score=80)
    res = InstantlyApiExporter({"campaign_id": "c"}, ctx).export([a, b], tmp_path)
    assert res.exported_ids == [b.id] and "1 failed" in res.detail


def test_instantly_scalar_custom_variables(make_ctx):
    ctx = make_ctx()
    lead = make_lead()
    payload = InstantlyApiExporter({"campaign_id": "c"}, ctx).build_payload(lead, "c", 3)
    assert isinstance(payload["custom_variables"]["score"], int)
    assert all(not isinstance(v, (dict, list)) for v in payload["custom_variables"].values())


# --- Smartlead CSV -------------------------------------------------------------------------

def test_smartlead_csv(make_ctx, tmp_path):
    ctx = make_ctx()
    lead = make_lead(steps=2)
    res = SmartleadCsvExporter({"type": "smartlead_csv"}, ctx).export(
        [lead, make_lead(domain="x.com", email=None)], tmp_path)
    assert res.exporter == "smartlead_csv" and res.count == 1 and res.exported_ids == [lead.id]
    assert res.path == str(tmp_path / "smartlead_upload.csv")
    assert read_header(res.path) == [
        "email", "first_name", "last_name", "company_name", "website", "location", "phone_number",
        "linkedin_profile", "company_url", "personalization", "job_title", "subject_1", "email_1",
        "email_2", "signal", "score", "tier",
    ]
    row = read_csv(res.path)[0]
    assert row["phone_number"] == "+44 161 496 0000" and row["company_url"] == "https://acme.com"
    assert row["linkedin_profile"] == "https://www.linkedin.com/in/jane-doe"
    assert row["job_title"] == "CFO" and row["email_2"] == "Follow-up 2 for Jane"


# --- Smartlead API -------------------------------------------------------------------------

def smartlead_ok(call: Dict[str, Any]) -> Dict[str, Any]:
    n = len(call["json"]["lead_list"])
    return {"ok": True, "upload_count": n, "total_leads": n, "already_added_to_campaign": 0,
            "duplicate_count": 0, "invalid_email_count": 0, "unsubscribed_leads": [],
            "is_lead_limit_exhausted": False, "lead_import_stopped_count": 0}


def many(n: int, **kw: Any) -> List[Lead]:
    return [make_lead(name=f"Co {i}", domain=f"co{i}.com", email=f"p{i}@co{i}.com", score=99 - (i % 50), **kw)
            for i in range(n)]


def test_smartlead_api_happy_path(make_ctx, tmp_path):
    ctx = make_ctx(env={"SMARTLEAD_API_KEY": "sl-key-9"})
    ctx.http.add("POST", SMARTLEAD_URL, fn=smartlead_ok)
    lead = make_lead(steps=2)
    res = SmartleadApiExporter({"type": "smartlead", "campaign_id": 4242}, ctx).export([lead], tmp_path)
    assert res.exporter == "smartlead" and res.count == 1 and res.exported_ids == [lead.id]
    assert res.detail == "uploaded 1/1 to campaign 4242"
    call = ctx.http.calls[0]
    assert call["url"] == SMARTLEAD_URL and "api_key" not in call["url"]
    assert call["params"] == {"api_key": "sl-key-9"}
    body = call["json"]
    assert body["settings"] == {"ignore_global_block_list": False, "ignore_unsubscribe_list": False,
                                "ignore_duplicate_leads_in_other_campaign": False}
    item = body["lead_list"][0]
    assert item["email"] == "jane@acme.com" and item["first_name"] == "Jane" and item["last_name"] == "Doe"
    assert item["phone_number"] == "+44 161 496 0000" and item["company_name"] == "Acme"
    assert item["website"] == "https://acme.com" and item["company_url"] == "https://acme.com"
    assert item["location"] == "Manchester, UK" and item["linkedin_profile"].endswith("/jane-doe")
    cf = item["custom_fields"]
    assert list(cf) == ["personalization", "job_title", "subject_1", "email_1", "email_2",
                        "signal", "score", "tier"]
    assert cf["score"] == "80" and cf["email_2"] == "Follow-up 2 for Jane"
    assert all(isinstance(v, str) for v in cf.values())


def test_smartlead_api_batches(make_ctx, tmp_path):
    ctx = make_ctx(env={"SMARTLEAD_API_KEY": "k"})
    ctx.http.add("POST", SMARTLEAD_URL, fn=smartlead_ok)
    leads = many(250)
    res = SmartleadApiExporter({"campaign_id": "4242"}, ctx).export(leads, tmp_path)
    assert [len(c["json"]["lead_list"]) for c in ctx.http.calls] == [100, 100, 50]
    assert res.count == 250 and set(res.exported_ids) == {ld.id for ld in leads}
    assert "uploaded 250/250" in res.detail
    ctx2 = make_ctx(env={"SMARTLEAD_API_KEY": "k"})
    ctx2.http.add("POST", SMARTLEAD_URL, fn=smartlead_ok)
    SmartleadApiExporter({"campaign_id": "4242", "batch_size": 2}, ctx2).export(many(5), tmp_path)
    assert [len(c["json"]["lead_list"]) for c in ctx2.http.calls] == [2, 2, 1]


def test_smartlead_api_response_counters_and_rejections(make_ctx, tmp_path):
    ctx = make_ctx(env={"SMARTLEAD_API_KEY": "k"})
    ctx.http.add("POST", SMARTLEAD_URL, json={
        "ok": True, "upload_count": 2, "total_leads": 4, "already_added_to_campaign": 1,
        "duplicate_count": 0, "invalid_email_count": 1, "unsubscribed_leads": ["p3@co3.com"],
        "is_lead_limit_exhausted": False, "lead_import_stopped_count": 0})
    leads = many(4)
    res = SmartleadApiExporter({"campaign_id": "4242"}, ctx).export(leads, tmp_path)
    assert res.detail == ("uploaded 2/4 to campaign 4242; already in campaign 1; invalid emails 1; "
                          "unsubscribed 1")
    assert leads[3].id not in res.exported_ids and len(res.exported_ids) == 3


def test_smartlead_api_failed_batch_is_excluded(make_ctx, tmp_path, caplog):
    ctx = make_ctx(env={"SMARTLEAD_API_KEY": "k"})
    ctx.http.add("POST", SMARTLEAD_URL, fn=smartlead_ok, times=1)
    ctx.http.add("POST", SMARTLEAD_URL, status=500, json={"message": "Internal server error"}, times=1)
    ctx.http.add("POST", SMARTLEAD_URL, json={"ok": False, "message": "Campaign is paused"}, times=1)
    ctx.http.add("POST", SMARTLEAD_URL, fn=smartlead_ok)
    leads = many(8)
    with caplog.at_level(logging.WARNING):
        res = SmartleadApiExporter({"campaign_id": "4242", "batch_size": 2}, ctx).export(leads, tmp_path)
    ordered = sorted(leads, key=lambda ld: -ld.score)
    assert res.exported_ids == [ordered[i].id for i in (0, 1, 6, 7)]
    assert "2 failed batch(es) (4 leads)" in res.detail and "uploaded 4/8" in res.detail
    assert "Internal server error" in caplog.text and "Campaign is paused" in caplog.text
    assert "k" not in [c["url"] for c in ctx.http.calls]


def test_smartlead_api_lead_limit_exhausted_stops(make_ctx, tmp_path):
    ctx = make_ctx(env={"SMARTLEAD_API_KEY": "k"})
    ctx.http.add("POST", SMARTLEAD_URL, json={"ok": True, "upload_count": 1, "total_leads": 2,
                                              "is_lead_limit_exhausted": True})
    res = SmartleadApiExporter({"campaign_id": "4242", "batch_size": 2}, ctx).export(many(6), tmp_path)
    assert res.exported_ids == [] and len(ctx.http.calls) == 1
    assert "lead limit exhausted" in res.detail and "4 not sent" in res.detail


def test_smartlead_api_unauthorized(make_ctx, tmp_path):
    ctx = make_ctx(env={"SMARTLEAD_API_KEY": "bad"})
    ctx.http.add("POST", SMARTLEAD_URL, status=401, text="Unauthorized")
    with pytest.raises(SmartleadError, match="SMARTLEAD_API_KEY"):
        SmartleadApiExporter({"campaign_id": "4242", "batch_size": 1}, ctx).export(many(3), tmp_path)
    assert len(ctx.http.calls) == 1


def test_smartlead_api_404_after_success_is_partial(make_ctx, tmp_path):
    ctx = make_ctx(env={"SMARTLEAD_API_KEY": "k"})
    ctx.http.add("POST", SMARTLEAD_URL, fn=smartlead_ok, times=1)
    ctx.http.add("POST", SMARTLEAD_URL, status=404, json={"error": "Campaign not found"})
    res = SmartleadApiExporter({"campaign_id": "4242", "batch_size": 1}, ctx).export(many(3), tmp_path)
    assert res.count == 1 and "stopped" in res.detail and "1 not sent" in res.detail


def test_smartlead_api_settings_override_and_paths(make_ctx, tmp_path):
    ctx = make_ctx(env={"SMARTLEAD_API_KEY": "k"})
    ctx.http.add("POST", "https://sl.example.com/api/v2/c/7/leads", fn=smartlead_ok)
    SmartleadApiExporter({"campaign_id": "7", "base_url": "https://sl.example.com/api/v2/",
                          "leads_path": "/c/{campaign_id}/leads",
                          "settings": {"ignore_global_block_list": True}}, ctx).export([make_lead()], tmp_path)
    s = ctx.http.calls[0]["json"]["settings"]
    assert s["ignore_global_block_list"] is True and s["ignore_unsubscribe_list"] is False


def test_smartlead_api_guards(make_ctx, tmp_path):
    ctx = make_ctx(dry_run=True)
    res = SmartleadApiExporter({}, ctx).export([make_lead()], tmp_path)
    assert res.detail == "dry-run" and res.count == 0 and ctx.http.calls == []
    ctx = make_ctx(env={"SMARTLEAD_API_KEY": "k"})
    with pytest.raises(ValueError, match="campaign_id"):
        SmartleadApiExporter({}, ctx).export([make_lead()], tmp_path)
    ctx = make_ctx()
    with pytest.raises(MissingCredentialError, match="SMARTLEAD_API_KEY"):
        SmartleadApiExporter({"campaign_id": "1"}, ctx).export([make_lead()], tmp_path)
    res = SmartleadApiExporter({"campaign_id": "1"}, ctx).export([make_lead(email=None)], tmp_path)
    assert res.count == 0 and "1 skipped" in res.detail and ctx.http.calls == []


def test_smartlead_api_network_error_batch(make_ctx, tmp_path):
    ctx = make_ctx(env={"SMARTLEAD_API_KEY": "k"})

    def boom(call):
        raise HttpError(0, call["url"], "ConnectTimeout: timed out")

    ctx.http.add("POST", SMARTLEAD_URL, fn=boom, times=1)
    ctx.http.add("POST", SMARTLEAD_URL, fn=smartlead_ok)
    res = SmartleadApiExporter({"campaign_id": "4242", "batch_size": 1}, ctx).export(many(2), tmp_path)
    assert res.count == 1 and "1 failed batch(es)" in res.detail


# --- Google Sheets (fake gspread) ----------------------------------------------------------

class WorksheetNotFound(Exception):
    pass


class SpreadsheetNotFound(Exception):
    pass


class FakeWorksheet:
    def __init__(self, title: str, rows: int = 1000, cols: int = 26, values: Optional[List[List[Any]]] = None):
        self.title, self.row_count, self.col_count = title, rows, cols
        self.values = [list(r) for r in (values or [])]
        self.calls: List[tuple] = []

    def clear(self):
        self.calls.append(("clear",))
        self.values = []

    def resize(self, rows=None, cols=None):
        self.calls.append(("resize", rows, cols))
        self.row_count, self.col_count = rows or self.row_count, cols or self.col_count

    def update(self, range_name=None, values=None, value_input_option=None, **kw):
        self.calls.append(("update", range_name, value_input_option))
        if len(values) > self.row_count or max(len(r) for r in values) > self.col_count:
            raise AssertionError("exceeds grid limits")
        self.values = [list(r) for r in values]

    def append_rows(self, values, value_input_option=None, table_range=None, **kw):
        self.calls.append(("append_rows", value_input_option, table_range, len(values)))
        self.values.extend(list(r) for r in values)

    def row_values(self, n):
        return list(self.values[n - 1]) if len(self.values) >= n else []

    def col_values(self, n):
        return [r[n - 1] if len(r) >= n else "" for r in self.values]

    def freeze(self, rows=None, cols=None):
        self.calls.append(("freeze", rows))


class FakeSpreadsheet:
    def __init__(self, key: str, worksheets: List[FakeWorksheet]):
        self.id = key
        self.url = f"https://docs.google.com/spreadsheets/d/{key}"
        self.sheets = {ws.title: ws for ws in worksheets}
        self.added: List[tuple] = []

    def worksheet(self, title):
        if title not in self.sheets:
            raise WorksheetNotFound(title)
        return self.sheets[title]

    def add_worksheet(self, title, rows, cols):
        self.added.append((title, rows, cols))
        ws = FakeWorksheet(title, rows, cols)
        self.sheets[title] = ws
        return ws


class FakeClient:
    def __init__(self, book: FakeSpreadsheet):
        self.book = book
        self.opened: List[tuple] = []
        # gspread >= 6 keeps the credentials on client.http_client.auth
        self.http_client = SimpleNamespace(
            auth=SimpleNamespace(service_account_email="leadgen-bot@proj.iam.gserviceaccount.com"))

    def open_by_key(self, key):
        self.opened.append(("key", key))
        if key != self.book.id:
            raise SpreadsheetNotFound(key)
        return self.book

    def open_by_url(self, url):
        self.opened.append(("url", url))
        if self.book.id not in url:
            raise SpreadsheetNotFound(url)
        return self.book


def fake_gspread(client: FakeClient) -> types.ModuleType:
    mod = types.ModuleType("gspread")
    exc = types.ModuleType("gspread.exceptions")
    exc.WorksheetNotFound = WorksheetNotFound
    exc.SpreadsheetNotFound = SpreadsheetNotFound
    mod.exceptions = exc
    mod.auth_calls = []

    def service_account(filename=None, **kw):
        mod.auth_calls.append(("file", filename))
        return client

    def service_account_from_dict(info, **kw):
        mod.auth_calls.append(("dict", info))
        return client

    mod.service_account = service_account
    mod.service_account_from_dict = service_account_from_dict
    return mod


@pytest.fixture
def sheets(monkeypatch, tmp_path):
    key_file = tmp_path / "sa.json"
    key_file.write_text('{"type": "service_account"}', encoding="utf-8")
    book = FakeSpreadsheet("1AbCdEfG", [FakeWorksheet("test")])
    client = FakeClient(book)
    mod = fake_gspread(client)
    monkeypatch.setitem(sys.modules, "gspread", mod)
    return SimpleNamespace(book=book, client=client, mod=mod, key_file=str(key_file))


def test_gsheets_replace(make_ctx, tmp_path, sheets):
    ctx = make_ctx()
    ws = sheets.book.sheets["test"]
    ws.values = [["old"], ["junk"]]
    a, b = make_lead(score=60), make_lead(name="Beta", domain="beta.io", score=95)
    exp = GoogleSheetsExporter({"type": "gsheets", "spreadsheet_id": "1AbCdEfG",
                                "service_account_file": sheets.key_file}, ctx)
    res = exp.export([a, b], tmp_path)
    assert sheets.mod.auth_calls == [("file", sheets.key_file)]
    assert sheets.client.opened == [("key", "1AbCdEfG")]
    # 29 columns > the default 26-column grid: grown before writing
    assert [c[0] for c in ws.calls] == ["clear", "resize", "update", "freeze"]
    assert ws.calls[1] == ("resize", 1000, 29) and ws.calls[2] == ("update", "A1", "RAW")
    header, rows = lead_rows([a, b], ctx.today)
    assert ws.values == [header] + rows
    assert ws.values[1][0] == b.id and isinstance(ws.values[1][1], int)
    assert res.exporter == "gsheets" and res.count == 2 and res.exported_ids == [b.id, a.id]
    assert res.path == "https://docs.google.com/spreadsheets/d/1AbCdEfG"
    assert res.detail == "worksheet 'test' replaced with 2 rows"


def test_gsheets_creates_missing_worksheet_and_grows_grid(make_ctx, tmp_path, sheets):
    ctx = make_ctx(env={"GOOGLE_APPLICATION_CREDENTIALS": sheets.key_file})
    exp = GoogleSheetsExporter({"spreadsheet_id": "1AbCdEfG", "worksheet": "Pipeline",
                                "freeze_header": False}, ctx)
    exp.export(many(3), tmp_path)
    assert sheets.book.added == [("Pipeline", 100, 29)]
    ws = sheets.book.sheets["Pipeline"]
    assert len(ws.values) == 4 and ("freeze", 1) not in ws.calls
    small = FakeWorksheet("Tiny", rows=2, cols=5)
    sheets.book.sheets["Tiny"] = small
    GoogleSheetsExporter({"spreadsheet_id": "1AbCdEfG", "worksheet": "Tiny"}, ctx).export(many(3), tmp_path)
    assert ("resize", 4, 29) in small.calls and len(small.values) == 4


def test_gsheets_open_by_url(make_ctx, tmp_path, sheets):
    ctx = make_ctx(env={"GOOGLE_APPLICATION_CREDENTIALS": sheets.key_file})
    url = "https://docs.google.com/spreadsheets/d/1AbCdEfG/edit#gid=0"
    GoogleSheetsExporter({"spreadsheet_url": url}, ctx).export([make_lead()], tmp_path)
    GoogleSheetsExporter({"spreadsheet_id": url}, ctx).export([make_lead()], tmp_path)
    assert sheets.client.opened == [("url", url), ("url", url)]


def test_gsheets_append_mode(make_ctx, tmp_path, sheets):
    ctx = make_ctx(env={"GOOGLE_APPLICATION_CREDENTIALS": sheets.key_file})
    a, b = make_lead(score=90), make_lead(name="Beta", domain="beta.io", score=80)
    cfg = {"spreadsheet_id": "1AbCdEfG", "mode": "append"}
    res = GoogleSheetsExporter(cfg, ctx).export([a], tmp_path)
    ws = sheets.book.sheets["test"]
    assert res.count == 1 and ws.values[0][0] == "lead_id" and len(ws.values) == 2
    assert ("append_rows", "RAW", "A1", 2) in ws.calls
    # second run: header kept, already-present lead skipped, rows re-ordered to the sheet header
    ws.values[0] = ["email", "lead_id", "company"]
    ws.values[1] = ["jane@acme.com", a.id, "Acme Inc"]
    res = GoogleSheetsExporter(cfg, ctx).export([a, b], tmp_path)
    assert res.count == 1 and res.exported_ids == [b.id]
    assert ws.values[-1] == ["jane@acme.com", b.id, "Beta"]
    assert res.detail == "worksheet 'test' appended 1 rows"
    # dedupe off -> both appended
    res = GoogleSheetsExporter(dict(cfg, dedupe=False), ctx).export([a, b], tmp_path)
    assert res.count == 2


def test_gsheets_service_account_json_env(make_ctx, tmp_path, sheets):
    info = {"type": "service_account", "client_email": "bot@x.iam.gserviceaccount.com"}
    ctx = make_ctx(env={"GOOGLE_SERVICE_ACCOUNT_JSON": json.dumps(info)})
    GoogleSheetsExporter({"spreadsheet_id": "1AbCdEfG"}, ctx).export([make_lead()], tmp_path)
    assert sheets.mod.auth_calls == [("dict", info)]
    ctx = make_ctx(env={"GOOGLE_SERVICE_ACCOUNT_JSON": "{not json"})
    with pytest.raises(GoogleSheetsError, match="not valid JSON"):
        GoogleSheetsExporter({"spreadsheet_id": "1AbCdEfG"}, ctx).export([make_lead()], tmp_path)


def test_gsheets_errors(make_ctx, tmp_path, sheets):
    ctx = make_ctx()
    with pytest.raises(MissingCredentialError, match="GOOGLE_APPLICATION_CREDENTIALS"):
        GoogleSheetsExporter({"spreadsheet_id": "1AbCdEfG"}, ctx).export([], tmp_path)
    with pytest.raises(MissingCredentialError, match="not found"):
        GoogleSheetsExporter({"spreadsheet_id": "1AbCdEfG",
                              "service_account_file": str(tmp_path / "nope.json")}, ctx).export([], tmp_path)
    with pytest.raises(ValueError, match="spreadsheet_id"):
        GoogleSheetsExporter({"service_account_file": sheets.key_file}, ctx).export([], tmp_path)
    with pytest.raises(ValueError, match="mode"):
        GoogleSheetsExporter({"spreadsheet_id": "x", "mode": "upsert"}, ctx).export([], tmp_path)
    with pytest.raises(GoogleSheetsError, match="leadgen-bot@proj.iam.gserviceaccount.com"):
        GoogleSheetsExporter({"spreadsheet_id": "WRONG", "service_account_file": sheets.key_file},
                             ctx).export([], tmp_path)


def test_gsheets_dry_run_makes_no_calls(make_ctx, tmp_path, sheets):
    ctx = make_ctx(dry_run=True)
    res = GoogleSheetsExporter({"spreadsheet_id": "1AbCdEfG"}, ctx).export([make_lead()], tmp_path)
    assert res.detail == "dry-run" and res.count == 0
    assert sheets.mod.auth_calls == [] and sheets.client.opened == []


def test_gsheets_without_gspread_installed(make_ctx, tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "gspread", None)  # import gspread -> ImportError
    ctx = make_ctx()
    with pytest.raises(RuntimeError, match="pip install gspread"):
        GoogleSheetsExporter({"spreadsheet_id": "x"}, ctx).export([make_lead()], tmp_path)
    # dry-run never imports it
    res = GoogleSheetsExporter({"spreadsheet_id": "x"}, make_ctx(dry_run=True)).export([], tmp_path)
    assert res.detail == "dry-run"


# --- webhook --------------------------------------------------------------------------------

def test_webhook_batches(make_ctx, tmp_path):
    ctx = make_ctx(env={"LEADGEN_EXPORT_WEBHOOK_URL": HOOK_URL})
    ctx.http.add("POST", HOOK_URL, text="Accepted")
    leads = many(120)
    res = WebhookExporter({"type": "webhook"}, ctx).export(leads, tmp_path)
    assert res.exporter == "webhook" and res.count == 120 and len(res.exported_ids) == 120
    assert res.detail == "delivered 120/120 to https://hook.eu1.make.com" and "SECRET" not in res.detail
    sizes = [len(c["json"]["leads"]) for c in ctx.http.calls]
    assert sizes == [50, 50, 20]
    body = ctx.http.calls[0]["json"]
    assert body["playbook"] == "test" and body["batch"] == 1 and body["batches"] == 3 and body["count"] == 50
    first = body["leads"][0]
    assert first["id"] == res.exported_ids[0] and first["company"]["name"] == "Co 0"
    assert first["company"]["signals"][0]["posted_at"] == "2026-09-20"
    assert first["flat"]["email"] == "p0@co0.com" and first["flat"]["subject_1"].startswith("accountant")
    assert first["flat"]["company_name"] == "Co 0" and first["flat"]["score"] == 99
    assert ctx.http.calls[0]["headers"]["Content-Type"] == "application/json"
    json.dumps(body)  # fully JSON-serialisable


def test_webhook_per_lead_headers_method(make_ctx, tmp_path):
    ctx = make_ctx()
    ctx.http.add("PUT", "https://n8n.example.com/webhook/leads", json={"ok": True})
    leads = many(3)
    res = WebhookExporter({"url": "https://n8n.example.com/webhook/leads", "per_lead": True, "method": "put",
                           "include_flat": False, "headers": {"X-Token": "t0k", "Content-Type": "application/json; charset=utf-8"}},
                          ctx).export(leads, tmp_path)
    assert res.count == 3 and len(ctx.http.calls) == 3
    call = ctx.http.calls[0]
    assert call["method"] == "PUT" and set(call["json"]) == {"playbook", "lead"}
    assert call["json"]["lead"]["contact"]["email"] == "p0@co0.com" and "flat" not in call["json"]["lead"]
    assert call["headers"] == {"X-Token": "t0k", "Content-Type": "application/json; charset=utf-8"}


def test_webhook_batch_size_config(make_ctx, tmp_path):
    ctx = make_ctx()
    ctx.http.add("POST", HOOK_URL, json={})
    WebhookExporter({"url": HOOK_URL, "batch_size": 2}, ctx).export(many(5), tmp_path)
    assert [c["json"]["count"] for c in ctx.http.calls] == [2, 2, 1]


def test_webhook_partial_failure(make_ctx, tmp_path, caplog):
    ctx = make_ctx()
    ctx.http.add("POST", HOOK_URL, status=500, text="Scenario error", times=1)
    ctx.http.add("POST", HOOK_URL, text="Accepted")
    leads = many(4)
    with caplog.at_level(logging.WARNING):
        res = WebhookExporter({"url": HOOK_URL, "batch_size": 2}, ctx).export(leads, tmp_path)
    assert res.count == 2 and "2 failed" in res.detail
    assert "SECRET" not in caplog.text and "Scenario error" in caplog.text


def test_webhook_fatal_and_network_errors_hide_url(make_ctx, tmp_path, caplog):
    ctx = make_ctx()
    ctx.http.add("POST", HOOK_URL, status=410, text="Gone")
    with pytest.raises(WebhookExportError) as ei:
        WebhookExporter({"url": HOOK_URL, "batch_size": 1}, ctx).export(many(3), tmp_path)
    assert "HTTP 410" in str(ei.value) and "SECRET" not in str(ei.value)
    assert len(ctx.http.calls) == 1

    ctx = make_ctx()

    def boom(call):
        raise HttpError(0, call["url"], f"ConnectionError: Max retries exceeded with url: {call['url']}")

    ctx.http.add("POST", HOOK_URL, fn=boom)
    with caplog.at_level(logging.WARNING):
        res = WebhookExporter({"url": HOOK_URL}, ctx).export(many(2), tmp_path)
    assert res.count == 0 and "2 failed" in res.detail
    assert "SECRET" not in caplog.text and "network error: ConnectionError" in caplog.text


def test_webhook_guards(make_ctx, tmp_path):
    ctx = make_ctx(dry_run=True)
    res = WebhookExporter({}, ctx).export(many(2), tmp_path)
    assert res.detail == "dry-run" and ctx.http.calls == []
    ctx = make_ctx()
    with pytest.raises(MissingCredentialError, match="LEADGEN_EXPORT_WEBHOOK_URL"):
        WebhookExporter({}, ctx).export(many(1), tmp_path)
    with pytest.raises(ValueError, match="http"):
        WebhookExporter({"url": "ftp://x"}, ctx).export(many(1), tmp_path)
    with pytest.raises(ValueError, match="method"):
        WebhookExporter({"url": HOOK_URL, "method": "DELETE"}, ctx).export(many(1), tmp_path)
    with pytest.raises(ValueError, match="headers"):
        WebhookExporter({"url": HOOK_URL, "headers": ["x"]}, ctx).export(many(1), tmp_path)
    res = WebhookExporter({}, ctx).export([], tmp_path)  # nothing to send: no URL needed
    assert res.count == 0 and ctx.http.calls == []


def test_webhook_url_env_override(make_ctx, tmp_path):
    ctx = make_ctx(env={"CLAY_HOOK": "https://api.clay.com/v3/sources/webhook/pull-in-data-from-a-webhook-1"})
    ctx.http.add("POST", re.compile(r"clay\.com"), json={"success": True})
    res = WebhookExporter({"url_env": "CLAY_HOOK"}, ctx).export(many(1), tmp_path)
    assert res.count == 1 and "https://api.clay.com" in res.detail


# --- exporters via the registry, as the pipeline calls them --------------------------------

def test_pipeline_style_invocation(make_ctx, tmp_path):
    ctx = make_ctx(outbound={"exporters": [{"type": "csv"}, {"type": "json"}, {"type": "instantly_csv"},
                                           {"type": "smartlead_csv"}]})
    leads = many(3)
    for cfg in ctx.playbook.outbound["exporters"]:
        exp = registry.create("exporter", cfg, ctx)
        res = exp.export(leads, tmp_path)
        assert res.count == 3 and len(res.exported_ids) == 3
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["instantly_upload.csv", "leads.json", "opportunities.csv", "smartlead_upload.csv"]


def test_gsheets_account_email_lookup_gspread5_and_6():
    old = SimpleNamespace(auth=SimpleNamespace(service_account_email="a@x.iam.gserviceaccount.com"))
    new = SimpleNamespace(http_client=SimpleNamespace(auth=SimpleNamespace(service_account_email="b@x.io")))
    assert GoogleSheetsExporter._account_email(old) == "a@x.iam.gserviceaccount.com"
    assert GoogleSheetsExporter._account_email(new) == "b@x.io"
    assert GoogleSheetsExporter._account_email(object()) == ""
