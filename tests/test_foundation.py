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


def test_parse_date_relative_to_given_today():
    assert parse_date("3 days ago", today=TODAY) == TODAY - timedelta(days=3)
    assert parse_date("yesterday", today=TODAY) == TODAY - timedelta(days=1)


def test_email_find_regex_is_linear_on_long_garbage():
    import time
    from leadgen.utils import EMAIL_FIND_RE
    t = time.time()
    assert EMAIL_FIND_RE.search("a" * 200_000) is None
    assert time.time() - t < 1.0
    assert EMAIL_FIND_RE.search("mail jane.doe@acme.com now").group(0) == "jane.doe@acme.com"


def test_store_first_seen_uses_posting_date_and_lost_is_sticky():
    s = Store(":memory:")
    sig = Signal(type="job_posting", title="Controller", external_id="x", posted_at="2026-09-01")
    s.observe_signals("acme.com", [sig], TODAY)
    assert sig.first_seen == date(2026, 9, 1)
    lead = Lead(company=Company(name="Acme", domain="acme.com"),
                contact=Contact(full_name="Jane Doe", email="jane@acme.com"), playbook="p", stage=Stage.READY)
    s.save_lead(lead)
    s.set_stage(lead.id, Stage.LOST)
    lead.stage = Stage.READY          # a later pipeline run re-saves it
    s.save_lead(lead)
    assert s.get_lead(lead.id).stage == Stage.LOST
    eng = s.company_engagement("acme.com", "p")
    assert Stage.LOST in eng["stages"] and eng["last_exported"] is None


def test_store_find_reply_roundtrip():
    s = Store(":memory:")
    r = Reply(from_email="Jane@acme.com", body="yes", received_at="2026-09-20T10:00:00Z",
              data={"campaign_id": "c1"})
    s.save_reply(r, "p")
    got = s.find_reply("p", "jane@acme.com", "2026-09-20T10:00:00Z")
    assert len(got) == 1 and got[0].data == {"campaign_id": "c1"}


def test_build_llm_passes_writer_llm_options(make_ctx):
    from leadgen.llm import build_llm
    ctx = make_ctx(env={"ANTHROPIC_API_KEY": "k"},
                   writer={"type": "ai", "provider": "anthropic", "llm": {"timeout": 33, "effort": "low"}})
    client = build_llm(ctx)
    assert client.config["timeout"] == 33 and client.config["effort"] == "low"
    assert client.config["type"] == "anthropic"


def test_http_network_error_never_leaks_query_keys():
    from leadgen.http import HttpClient, HttpError
    client = HttpClient(retries=0)
    try:
        client.get("http://127.0.0.1:1/v2/domain-search?domain=acme.com&api_key=SECRETKEY123")
    except HttpError as e:
        assert "SECRETKEY123" not in str(e) and "SECRETKEY123" not in e.body
        assert e.status == 0
    else:  # pragma: no cover
        raise AssertionError("expected HttpError")


def test_safe_url_and_redact():
    from leadgen.http import redact, safe_url
    fake_hook = "https://hooks.slack.com/" + "services/" + "T024BE7LD/B01234567/" + "abcdefghijklmnop" + "qrstuvwx"
    assert "T024BE7LD" not in safe_url(fake_hook) and "qrstuvwx" not in safe_url(fake_hook)
    assert safe_url("https://api.apollo.io/api/v1/mixed_people/api_search") == \
        "https://api.apollo.io/api/v1/mixed_people/api_search"
    assert "xyz" not in redact("Authorization: Bearer xyz.abc") and "k1" not in redact("?api_key=k1&x=1")


def test_secret_is_stripped(make_ctx):
    ctx = make_ctx(env={"FOO_KEY": "  abc\n", "EMPTY": "  "})

    class A(Adapter):
        name = "a"
        env_key = "FOO_KEY"

    assert A({}, ctx).secret() == "abc"
    assert not A({"api_key_env": "EMPTY"}, ctx).has_secret()


def test_yaml_on_key_is_not_a_boolean(tmp_path):
    from leadgen.playbook import load_playbook
    p = tmp_path / "p.yaml"
    p.write_text("name: x\nnotify:\n  on: [positive]\n", encoding="utf-8")
    assert load_playbook(str(p)).notify["on"] == ["positive"]


def test_personal_email_detection_and_domain_junk():
    from leadgen.utils import is_personal_email
    for e in ("bob@gmail.com", "x@yahoo.fr", "y@sbcglobal.net", "z@web.de", "q@hotmail.co.uk"):
        assert is_personal_email(e), e
    for e in ("jane@acme.com", "jane@mail.acme.com", "a@outlook-partners.com"):
        assert not is_personal_email(e), e
    assert normalize_domain("[none]") == "" and normalize_domain("acme.com]") == ""
    assert parse_date("05/06/2026", date_order="mdy") == date(2026, 5, 6)
    assert parse_date("05/06/2026") == date(2026, 6, 5)


def test_contact_full_name_split_handles_honorifics():
    assert (Contact(full_name="Dr. Jane Doe").first_name, Contact(full_name="Dr. Jane Doe").last_name) == ("Jane", "Doe")
    c = Contact(full_name="Doe, Jane")
    assert (c.first_name, c.last_name) == ("Jane", "Doe")


def test_unsuppress_is_kind_aware_and_followups_hide_suppressed():
    s = Store(":memory:")
    s.suppress("acme.com", "domain")
    s.suppress("jane@acme.com", "email")
    assert s.unsuppress("jane@acme.com") == 1
    assert s.is_suppressed(domain="acme.com")
    s.schedule_followup(TODAY, "timing", email="bob@beta.com", playbook="p")
    s.suppress("bob@beta.com", "email")
    assert s.due_followups(TODAY, "p") == []
    assert len(s.due_followups(TODAY, "p", include_suppressed=True)) == 1


def test_sibling_postings_in_one_batch_are_not_reposts():
    s = Store(":memory:")
    a = Signal(type="job_posting", title="Accountant", external_id="1", location="NYC")
    b = Signal(type="job_posting", title="Accountant", external_id="2", location="LA")
    s.observe_signals("acme.com", [a, b], TODAY)
    assert not a.reposted and not b.reposted
    # next run: id 1 disappeared, id 3 appeared -> genuine re-post
    c = Signal(type="job_posting", title="Accountant", external_id="3")
    d = Signal(type="job_posting", title="Accountant", external_id="2")
    s.observe_signals("acme.com", [c, d], TODAY + timedelta(days=10))
    assert c.reposted


def test_notify_skips_disabled_channels(make_ctx, capsys):
    from leadgen.notify import notify
    ctx = make_ctx(notify={"channels": [{"type": "console", "enabled": False}], "on": ["positive"]})
    assert notify(ctx, "positive", "t", "x") == 0
    assert "t" not in capsys.readouterr().out


def test_region_names_of_other_countries():
    from leadgen.filters import place_ids
    assert "canada" in place_ids("Vancouver, British Columbia")
    assert "australia" in place_ids("Brisbane, Queensland")
    assert "india" in place_ids("Pune, Maharashtra")
    assert "germany" in place_ids("Munich, Bavaria")
    assert "united states" in place_ids("Austin, TX") and not place_ids("Houston")


def test_people_csv_with_separate_job_title_column_keeps_the_signal(make_ctx, tmp_path):
    from leadgen.sources.csv_source import CsvSource
    p = tmp_path / "p.csv"
    p.write_text("First Name,Last Name,Title,Email,Company,Website,Job Title,Date Posted\n"
                 "Jane,Doe,Founder,jane@acme.com,Acme Agency,acme.com,Business Development Manager,2 days ago\n",
                 encoding="utf-8")
    [c] = CsvSource({"type": "csv", "path": str(p)}, make_ctx()).fetch()
    assert [(s.type, s.title) for s in c.signals] == [("job_posting", "Business Development Manager")]
    assert c.contacts[0].title == "Founder"


def test_ai_reply_classifier_retries_truncated_output(make_ctx):
    from leadgen.llm.base import LLMTruncatedError
    from leadgen.replies import classify_ai
    from tests.fakes import FakeLLM
    ctx = make_ctx()
    ctx.llm = FakeLLM(LLMTruncatedError("cut"), {"category": "positive", "confidence": 0.9, "summary": "yes"})
    r = classify_ai(Reply(from_email="a@b.com", body="Sounds great, send details"), ctx)
    assert r.category == "positive" and r.classifier == "ai"
    assert ctx.llm.calls[1]["max_tokens"] == 2 * ctx.llm.calls[0]["max_tokens"]
