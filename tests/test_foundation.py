from datetime import date, timedelta

import pytest

from leadgen import registry
from leadgen.context import MissingCredentialError, Adapter
from leadgen.models import Company, Contact, Lead, Signal, Stage, Reply, ReplyCategory
from leadgen.playbook import PlaybookError, expand_env, from_dict
from leadgen.store import Store
from leadgen.utils import (company_key, contains_any, get_path, normalize_company_name,
                           normalize_domain, parse_date, to_int)
from tests.conftest import TODAY


def test_utils_normalization():
    assert normalize_domain("https://www.Acme.com/about") == "acme.com"
    assert normalize_domain("jane@Sub.Acme.co.uk") == "sub.acme.co.uk"
    assert normalize_company_name("The Acme Group, Inc.") == "acme"
    assert company_key("Acme Inc", "") == "name:acme"
    assert company_key("Acme Inc", "www.acme.com") == "acme.com"
    assert to_int("1,200") == 1200 and to_int("51-200") == 51 and to_int("10k") == 10000
    assert to_int(None) is None and to_int("") is None
    assert parse_date("2026-09-20T10:00:00Z") == date(2026, 9, 20)
    assert parse_date(1758700000) is not None and parse_date(1758700000000) is not None
    assert parse_date("3 days ago") == date.today() - timedelta(days=3)
    assert parse_date("garbage") is None
    assert contains_any("Senior Financial Accountant", ["accountant"]) == "accountant"
    assert contains_any("Accountants wanted", ["accountant"]) is None  # word boundary
    assert get_path({"a": {"b": [{"c": 1}]}}, "a.b.0.c") == 1
    assert get_path({"a": None}, "a.b", "d") == "d"


def test_models_roundtrip_and_merge():
    from leadgen.pipeline import merge_companies

    c = Company(name="Acme", website="https://acme.com", employees="51-200",
                signals=[Signal(type="job_posting", title="Accountant", posted_at="2026-09-20")],
                contacts=[Contact(full_name="Jane Doe", email="JANE@acme.com", title="CFO")])
    assert c.domain == "acme.com" and c.employees == 51
    assert c.contacts[0].first_name == "Jane" and c.contacts[0].email == "jane@acme.com"
    lead = Lead(company=c, contact=c.contacts[0], playbook="p")
    back = Lead.from_dict(lead.to_dict())
    assert back.id == lead.id and back.company.signals[0].posted_at == date(2026, 9, 20)
    other = Company(name="Acme Inc", domain="acme.com", industry="Manufacturing",
                    signals=[Signal(type="funding", title="Series A")])
    merged = merge_companies([c, other, Company(name="Other", domain="other.com")])
    assert len(merged) == 2 and merged[0].industry == "Manufacturing" and len(merged[0].signals) == 2
    # same name, different domains => kept apart
    assert len(merge_companies([Company(name="Apex", domain="apex.com"), Company(name="Apex", domain="apex.io")])) == 2


def test_playbook_defaults_env_and_validation():
    pb = from_dict({"name": "x", "offer": {"booking_link": "${LINK:-https://cal.com/me}"}}, env={})
    assert pb.offer["booking_link"] == "https://cal.com/me"
    assert pb.scoring["tiers"]["hot"] == 80 and pb.writer["type"] == "template"
    assert expand_env("${A}", {"A": "1"}) == "1"
    with pytest.raises(PlaybookError):
        from_dict({"name": "bad name!"})
    with pytest.raises(PlaybookError):
        from_dict({"name": "x", "writer": {"type": "ai"}})
    with pytest.raises(PlaybookError):
        from_dict({"name": "x", "scoring": {"tiers": {"hot": 50, "normal": 70}}})


def test_registry_and_secrets(make_ctx):
    ctx = make_ctx(env={"FOO_KEY": "abc"})
    assert "csv" in registry.available("source")
    with pytest.raises(registry.UnknownAdapterError):
        registry.resolve("source", "nope")

    class A(Adapter):
        name = "a"
        env_key = "FOO_KEY"

    assert A({}, ctx).secret() == "abc"
    assert A({"api_key": "cfg"}, ctx).secret() == "cfg"
    with pytest.raises(MissingCredentialError):
        A({"api_key_env": "MISSING"}, ctx).secret()


def test_store_lifecycle():
    s = Store(":memory:")
    run = s.start_run("p")
    c = Company(name="Acme", domain="acme.com")
    lead = Lead(company=c, contact=Contact(full_name="Jane Doe", email="jane@acme.com"), playbook="p",
                run_id=run, stage=Stage.VERIFIED, score=85, tier="hot")
    s.save_lead(lead)
    s.finish_run(run, {"sourced": 1})
    assert s.latest_run_id("p") == run
    assert [ld.id for ld in s.leads_for_run(run)] == [lead.id]
    assert not s.recently_contacted("jane@acme.com", 90, TODAY)
    s.mark_exported(lead.id, "csv")
    assert s.recently_contacted("jane@acme.com", 90, date.today())
    assert not s.recently_contacted("jane@acme.com", 90, date.today(), exclude_lead_id=lead.id)
    # stage never goes backwards via save_lead
    lead.stage = Stage.QUALIFIED
    s.save_lead(lead)
    assert s.get_lead(lead.id).stage == Stage.EXPORTED
    assert s.set_stage(lead.id, Stage.POSITIVE)
    assert not s.set_stage(lead.id, Stage.REPLIED)
    f = s.funnel("p")
    assert f["stages"]["positive"] == 1 and f["stages"]["sourced"] == 1 and f["stages"]["booked"] == 0
    s.set_stage(lead.id, Stage.LOST)
    assert s.funnel("p")["stages"]["positive"] == 1 and s.funnel("p")["lost"] == 1
    # suppression
    s.suppress("Bad.com", "domain", "competitor")
    assert s.is_suppressed(email="x@bad.com") and s.is_suppressed(domain="https://bad.com")
    assert not s.is_suppressed(email="x@good.com")
    # replies + follow-ups
    s.save_reply(Reply(from_email="jane@acme.com", body="yes", category=ReplyCategory.POSITIVE), "p")
    assert s.funnel("p")["replies"] == {"positive": 1}
    s.schedule_followup(TODAY, "timing", lead_id=lead.id, playbook="p")
    assert len(s.due_followups(TODAY)) == 1
    # verification cache
    s.put_verification("jane@acme.com", "valid", "mv")
    assert s.get_verification("JANE@acme.com") == "valid"


def test_store_signal_history_repost_detection():
    s = Store(":memory:")
    first = Signal(type="job_posting", title="Accountant", external_id="a1", posted_at="2026-09-01")
    s.observe_signals("acme.com", [first], date(2026, 9, 2))
    assert first.first_seen == date(2026, 9, 1) or first.first_seen == date(2026, 9, 2)
    assert not first.reposted
    again = Signal(type="job_posting", title="Accountant ", external_id="a2", posted_at="2026-09-20")
    s.observe_signals("acme.com", [again], date(2026, 9, 21))
    assert again.reposted and again.first_seen <= date(2026, 9, 2)
    undated = Signal(type="funding", title="Series A")
    s.observe_signals("acme.com", [undated], date(2026, 9, 21))
    assert undated.first_seen == date(2026, 9, 21) and not undated.reposted
