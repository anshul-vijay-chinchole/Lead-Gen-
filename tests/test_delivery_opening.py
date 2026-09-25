"""Tests for leadgen.delivery.opening: template lines, AI lines, the cost cap and fallbacks."""
from __future__ import annotations

import re
from datetime import date
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from leadgen.context import MissingCredentialError
from leadgen.delivery.opening import (DEFAULT_MAX_TOKENS, MAX_CHARS, MAX_WORDS, SYSTEM_PROMPT, OpeningStats,
                                      add_opening_lines, build_prompt, clean_ai_line, template_line,
                                      worst_case_cost)
from leadgen.delivery.rows import build_row
from leadgen.llm.base import LLMConfigError, LLMError, LLMTruncatedError
from leadgen.models import Company, Contact, EmailStatus, Lead, Signal
from leadgen.usage import BudgetExceeded, UsageMeter
from tests.conftest import TODAY
from tests.fakes import FakeLLM

GOOD = "Saw Acme Corp is hiring a Senior Accountant in Austin, TX, happy to share how we fill these roles fast."


# --- builders ---------------------------------------------------------------------------

def make_lead(name: str = "Acme Corp", *, titles: Optional[List[str]] = None, posted: Any = date(2026, 9, 22),
              location: str = "Austin, TX", signal_type: str = "job_posting", description: str = "",
              first_seen: Any = None, company_description: str = "", employees: Optional[int] = 120) -> Lead:
    titles = ["Senior Accountant"] if titles is None else titles
    sigs = [Signal(type=signal_type, title=t, posted_at=posted, first_seen=first_seen, location=location,
                   url="https://jobs.example/1", description=description, source="theirstack")
            for t in titles]
    company = Company(name=name, domain=f"{re.sub('[^a-z]', '', name.lower()) or 'x'}.example",
                      location=location, industry="Accounting", employees=employees,
                      description=company_description, signals=sigs)
    contact = Contact(full_name="Jane Doe", title="CFO", email="jane.doe@acme.example",
                      email_status=EmailStatus.VALID, source="apollo",
                      linkedin_url="https://www.linkedin.com/in/jane-doe")
    return Lead(company=company, contact=contact, score=90, tier="hot", playbook="client-acme")


def row_for(lead: Lead) -> Dict[str, Any]:
    return build_row(lead, TODAY, include_opening=True)


def rows_and_leads(n: int):
    leads = [make_lead(f"Company {chr(65 + i)}") for i in range(n)]
    rows = [row_for(ld) for ld in leads]
    return rows, {r["_lead_id"]: ld for r, ld in zip(rows, leads)}


def client(ai: bool = True, max_cost: Any = 0.50, **extra: Any) -> SimpleNamespace:
    return SimpleNamespace(name="acme", opening_line={"enabled": True, "ai": ai, "max_cost_usd": max_cost, **extra})


class UsageLLM(FakeLLM):
    """FakeLLM that reports token usage like a real client (``last_usage``)."""

    model = "test-model"

    def __init__(self, *responses: Any, usage: Optional[Dict[str, int]] = None) -> None:
        super().__init__(*responses)
        self.usage = usage
        self.last_usage: Any = None

    def complete(self, system: str, user: str, **kw: Any) -> str:
        out = super().complete(system, user, **kw)
        if self.usage is not None:
            self.last_usage = dict(self.usage)
        return out


def priced_ctx(make_ctx, dry_run: bool = False, output_price: float = 250.0, **kw: Any):
    """Context where test-model costs $0 input and ``output_price`` USD per million output tokens,
    so the worst case of one call is exactly max_tokens * output_price / 1e6 ($0.10 by default)."""
    return make_ctx(mode="delivery", dry_run=dry_run,
                    usage={"llm_price_per_mtok": {"test-model": {"input": 0.0, "output": output_price}}}, **kw)


# --- template_line ----------------------------------------------------------------------

def test_template_line_example_from_the_contract():
    assert template_line(row_for(make_lead())) == \
        "Saw Acme Corp is hiring a Senior Accountant in Austin, TX (posted 2 days ago)."


@pytest.mark.parametrize("title,expected", [
    ("Accountant", "an Accountant"),
    ("Office Manager", "an Office Manager"),
    ("HR Manager", "an HR Manager"),
    ("UX Designer", "a UX Designer"),
    ("University Recruiter", "a University Recruiter"),
    ("Payroll Specialist", "a Payroll Specialist"),
    ("8-hour Shift Lead", "an 8-hour Shift Lead"),
])
def test_template_line_articles(title, expected):
    line = template_line(row_for(make_lead(titles=[title], posted=TODAY)))
    assert line == f"Saw Acme Corp is hiring {expected} in Austin, TX (posted today)."


def test_template_line_several_roles_and_more_suffix():
    line = template_line(row_for(make_lead(titles=["Senior Accountant", "Staff Accountant", "Controller"])))
    assert line == ("Saw Acme Corp is hiring for 3 roles, including a Senior Accountant in Austin, TX "
                    "(posted 2 days ago).")
    seven = template_line(row_for(make_lead(titles=[f"Accountant {i}" for i in range(7)])))
    assert "hiring for 7 roles, including an Accountant 0" in seven


def test_template_line_missing_pieces_leave_no_dangling_text():
    lead = make_lead(location="")
    lead.company.location = ""
    assert template_line(row_for(lead)) == "Saw Acme Corp is hiring a Senior Accountant (posted 2 days ago)."
    assert template_line(row_for(make_lead(posted=None))) == \
        "Saw Acme Corp is hiring a Senior Accountant in Austin, TX."
    assert template_line(row_for(make_lead(posted=None, first_seen=date(2026, 9, 21)))) == \
        "Saw Acme Corp is hiring a Senior Accountant in Austin, TX (first seen 3 days ago)."
    assert template_line(row_for(make_lead(titles=[""], posted=TODAY))) == \
        "Saw Acme Corp is hiring in Austin, TX (posted today)."
    assert template_line({"company": "Acme", "signal_type": "Hiring", "job_titles": "Controller",
                          "date_posted": "2026-09-20"}) == "Saw Acme is hiring a Controller (posted on 2026-09-20)."
    assert template_line({}) == "Came across your company."
    assert template_line({"company": "Acme"}) == "Came across Acme."
    assert template_line({"signal_type": "Hiring", "job_titles": "Controller"}) == "Saw you are hiring a Controller."
    # odd inputs never crash
    assert template_line({"company": "Acme", "signal_type": "Hiring", "job_titles": "(+1 more)"}) == \
        "Saw Acme is hiring."
    assert template_line({"company": "Acme", "signal_type": "Hiring", "job_titles": " ; (+4 more)"}) == \
        "Saw Acme is hiring for 4 roles."
    assert template_line({"company": None, "signal_type": None, "job_titles": None, "location": None,
                          "posted": None, "date_posted": None}) == "Came across your company."
    assert template_line({"company": "Acme", "signal_type": "Hiring", "posted": "posted 3 days ago",
                          "date_posted": "not a date"}) == "Saw Acme is hiring (posted 3 days ago)."


def test_template_line_remote_and_non_hiring_signals():
    assert template_line(row_for(make_lead(location="Remote", posted=TODAY))) == \
        "Saw Acme Corp is hiring a remote Senior Accountant (posted today)."
    assert template_line({"company": "Acme", "signal_type": "Hiring", "location": "Remote - US"}) == \
        "Saw Acme is hiring remotely."
    funding = row_for(make_lead(titles=["Raised $20M Series B"], signal_type="funding",
                                posted=date(2026, 9, 21)))
    assert template_line(funding) == \
        "Saw the recent funding news at Acme Corp: Raised $20M Series B (posted 3 days ago)."
    assert template_line({"company": "Acme", "signal_type": "Other", "posted": "posted today"}) == \
        "Saw the recent news at Acme (posted today)."


def test_template_line_cleans_messy_values_and_is_factual():
    row = {"company": '  "Acme\nCorp"  ', "signal_type": "Hiring", "job_titles": "Senior Accountant.\n; ;",
           "location": " Austin,  TX, ", "posted": "posted 1 day ago"}
    assert template_line(row) == "Saw Acme Corp is hiring a Senior Accountant in Austin, TX (posted 1 day ago)."
    for lead in (make_lead(), make_lead(location=""), make_lead(titles=[]), make_lead(posted=None),
                 make_lead("Zoë & Søn Ünïcødé", titles=["Comptable (H/F)"])):
        row = row_for(lead)
        line = template_line(row)
        assert line.endswith(".") and not line.endswith("..")
        for bad in (" in ,", "()", " ,", "  ", " .", "(,", "in (", "None"):
            assert bad not in line, (bad, line)
        # every capitalised word comes from the row (no invented facts)
        facts = " ".join(str(v) for v in row.values())
        for word in re.findall(r"[A-Z][\w-]+", line):
            assert word in facts or word in ("Saw", "Came"), (word, line)


# --- clean_ai_line / prompt -------------------------------------------------------------

def test_clean_ai_line_strips_quotes_newlines_labels_and_emoji():
    assert clean_ai_line('"Saw Acme is hiring a Senior Accountant in Austin - happy to help."') == \
        "Saw Acme is hiring a Senior Accountant in Austin - happy to help."
    assert clean_ai_line("Here is an opening line:\n\n“Saw you’re hiring a Controller \U0001F680 in Austin!”\n") \
        == "Saw you're hiring a Controller in Austin!"
    assert clean_ai_line("Opening line: Noticed the Senior Accountant role at Acme") == \
        "Noticed the Senior Accountant role at Acme."
    assert clean_ai_line("- **Saw** your \"Senior Accountant\" opening in Austin") == \
        "Saw your Senior Accountant opening in Austin."


@pytest.mark.parametrize("answer", ["", None, "   ", "Hi [Name], saw your role.", "Saw it at https://acme.com today",
                                    "Email me at a@b.com about the role", "Great role!", "{company} is hiring now"])
def test_clean_ai_line_rejects_unusable_answers(answer):
    assert clean_ai_line(answer) == ""


def test_clean_ai_line_caps_length_and_rejects_invented_numbers():
    long = "Saw Acme is hiring a Senior Accountant in Austin. " + " ".join(["really"] * 60) + " great."
    out = clean_ai_line(long)
    assert out == "Saw Acme is hiring a Senior Accountant in Austin."
    one_sentence = " ".join(["word"] * 80)
    out = clean_ai_line(one_sentence)
    assert len(out.split()) <= MAX_WORDS and len(out) <= MAX_CHARS and out.endswith(".")
    facts = "- Company: Acme\n- Posted: posted 2 days ago\n- Company size: about 120 employees"
    assert clean_ai_line("Saw Acme posted the role 2 days ago with 120 people on board.", facts)
    assert clean_ai_line("Saw Acme is hiring 12 accountants after raising money.", facts) == ""


def test_prompt_has_the_rules_and_only_company_facts():
    lead = make_lead(titles=["Senior Accountant", "Staff Accountant"], description="Own month-end close. " * 60,
                     company_description="Family-owned distributor.")
    row = row_for(lead)
    system, user = build_prompt(row, lead)
    for rule in ("recruiter", "30 words", "Never invent", "no quotation marks", "no emoji", "Plain text"):
        assert rule in system
    assert "- Company: Acme Corp" in user
    assert "- Hiring for: Senior Accountant; Staff Accountant" in user
    assert "- Location: Austin, TX" in user
    assert "- Posted: posted 2 days ago" in user
    assert "- Company size: about 120 employees" in user
    assert "- About the company: Family-owned distributor." in user
    excerpt = re.search(r"- From the job ad: (.*)", user).group(1)
    assert excerpt.startswith("Own month-end close.") and len(excerpt) <= 400
    # no personal data goes to the AI
    for private in ("Jane", "Doe", "jane.doe@acme.example", "linkedin", "CFO"):
        assert private not in user
    # empty facts are left out
    _, bare = build_prompt({"company": "Acme", "signal_type": "Hiring", "job_titles": "Controller"})
    assert "Location" not in bare and "Posted" not in bare and "Industry" not in bare


def test_worst_case_cost():
    meter = UsageMeter(llm_prices={"m": (2.0, 10.0)})
    # (8 + 12) chars / 4 = 5 input tokens; 100 output tokens
    assert worst_case_cost("x" * 8, "y" * 12, "m", 100, meter) == pytest.approx((5 * 2.0 + 100 * 10.0) / 1e6)
    assert worst_case_cost("x", "", "unknown-model", 10) == pytest.approx((1 * 10.0 + 10 * 50.0) / 1e6)


# --- add_opening_lines: template path ---------------------------------------------------

def test_template_only_when_ai_is_off(make_ctx):
    ctx = make_ctx(mode="delivery")
    llm = FakeLLM()                      # would fail the test if it were called
    ctx.llm = llm
    rows, leads = rows_and_leads(3)
    stats = add_opening_lines(rows, leads, ctx, client(ai=False))
    assert llm.calls == []
    assert stats == OpeningStats(ai_lines=0, template_lines=3, cost_usd=0.0, capped=False, errors=0)
    assert all(r["opening_line"] == template_line(r) and r["_opening_source"] == "template" for r in rows)


def test_ai_never_called_in_dry_run(make_ctx):
    ctx = make_ctx(mode="delivery", dry_run=True)
    llm = FakeLLM()
    ctx.llm = llm
    rows, leads = rows_and_leads(2)
    stats = add_opening_lines(rows, leads, ctx, client(ai=True))
    assert llm.calls == [] and stats.ai_lines == 0 and stats.template_lines == 2 and stats.errors == 0
    assert any("dry run" in n for n in stats.notes)
    assert rows[0]["opening_line"].startswith("Saw Company A is hiring")


def test_ai_requested_but_no_llm_configured(make_ctx):
    ctx = make_ctx(mode="delivery")
    ctx.llm = None
    rows, leads = rows_and_leads(2)
    stats = add_opening_lines(rows, leads, ctx, client(ai=True))
    assert stats.template_lines == 2 and stats.ai_lines == 0 and not stats.capped
    assert any("writer.provider" in n for n in stats.notes)


def test_broken_llm_config_never_raises():
    class BrokenCtx:
        dry_run = False
        usage = UsageMeter()
        playbook = SimpleNamespace(writer={})

        @property
        def llm(self):
            raise ValueError("unknown llm type 'gpt-foo'")

    rows, leads = rows_and_leads(1)
    stats = add_opening_lines(rows, leads, BrokenCtx(), client(ai=True))
    assert stats.template_lines == 1 and any("gpt-foo" in n for n in stats.notes)


def test_zero_cost_cap_means_no_ai(make_ctx):
    ctx = make_ctx(mode="delivery")
    llm = FakeLLM()
    ctx.llm = llm
    rows, leads = rows_and_leads(2)
    stats = add_opening_lines(rows, leads, ctx, client(ai=True, max_cost=0))
    assert llm.calls == [] and stats.capped and stats.template_lines == 2


def test_empty_rows_and_missing_client(make_ctx):
    ctx = make_ctx(mode="delivery")
    assert add_opening_lines([], {}, ctx, client()) == OpeningStats()
    rows, _ = rows_and_leads(1)
    stats = add_opening_lines(rows, None, ctx, None)
    assert stats.template_lines == 1 and rows[0]["opening_line"]


# --- add_opening_lines: AI path ---------------------------------------------------------

def test_ai_lines_happy_path_records_usage(make_ctx):
    ctx = priced_ctx(make_ctx)
    llm = UsageLLM(GOOD, "\"Noticed Company B is hiring a Senior Accountant in Austin, TX.\"",
                   usage={"input_tokens": 300, "output_tokens": 40})
    ctx.llm = llm
    rows, leads = rows_and_leads(2)
    stats = add_opening_lines(rows, leads, ctx, client(ai=True))
    assert stats.ai_lines == 2 and stats.template_lines == 0 and stats.errors == 0 and not stats.capped
    assert rows[0]["opening_line"] == GOOD and rows[0]["_opening_source"] == "ai"
    assert rows[1]["opening_line"] == "Noticed Company B is hiring a Senior Accountant in Austin, TX."
    # one short call per row, with the output budget and the row's facts
    assert len(llm.calls) == 2
    assert all(c["max_tokens"] == DEFAULT_MAX_TOKENS and c["system"] == SYSTEM_PROMPT for c in llm.calls)
    assert "- Company: Company A" in llm.calls[0]["user"] and "- Company: Company B" in llm.calls[1]["user"]
    # actual cost from last_usage: 40 output tokens x $250 / M = $0.01 per call
    assert stats.cost_usd == pytest.approx(0.02)
    usage = {(r["kind"], r["type"]): r for r in ctx.usage.rows()}
    assert usage[("llm", "fake-llm")]["input_tokens"] == 600
    assert usage[("llm", "fake-llm")]["output_tokens"] == 80
    assert ctx.usage.estimated_cost == pytest.approx(0.02)


def test_cost_cap_pre_check_with_worst_case_charges(make_ctx):
    """No usage reported: every call is charged the worst case ($0.10); cap $0.25 -> 2 AI lines."""
    ctx = priced_ctx(make_ctx)
    llm = FakeLLM(*([GOOD] * 5))
    llm.model = "test-model"
    ctx.llm = llm
    rows, leads = rows_and_leads(5)
    stats = add_opening_lines(rows, leads, ctx, client(ai=True, max_cost=0.25))
    assert len(llm.calls) == 2
    assert stats.ai_lines == 2 and stats.template_lines == 3 and stats.capped
    assert stats.cost_usd == pytest.approx(0.20)
    assert stats.cost_usd <= 0.25
    assert [r["_opening_source"] for r in rows] == ["ai", "ai", "template", "template", "template"]
    assert any("cost cap" in n for n in stats.notes)


def test_cost_cap_uses_actual_usage_when_reported(make_ctx):
    """Actual $0.01 per call, worst case $0.10: calls continue while spent + 0.10 <= 0.25 -> 16 calls."""
    ctx = priced_ctx(make_ctx)
    llm = UsageLLM(*([GOOD] * 20), usage={"input_tokens": 500, "output_tokens": 40})
    ctx.llm = llm
    rows, leads = rows_and_leads(20)
    stats = add_opening_lines(rows, leads, ctx, client(ai=True, max_cost=0.25))
    assert len(llm.calls) == 16
    assert stats.ai_lines == 16 and stats.template_lines == 4 and stats.capped
    assert stats.cost_usd == pytest.approx(0.16)


def test_cap_is_checked_before_the_first_call(make_ctx):
    ctx = priced_ctx(make_ctx)
    llm = UsageLLM()
    ctx.llm = llm
    rows, leads = rows_and_leads(2)
    stats = add_opening_lines(rows, leads, ctx, client(ai=True, max_cost=0.05))   # worst case is $0.10
    assert llm.calls == [] and stats.capped and stats.cost_usd == 0 and stats.template_lines == 2


def test_errors_fall_back_to_template(make_ctx):
    ctx = priced_ctx(make_ctx)
    llm = UsageLLM(LLMError("HTTP 500"), "Hi [Name], great role!", GOOD, usage={"input_tokens": 1, "output_tokens": 4})
    ctx.llm = llm
    rows, leads = rows_and_leads(3)
    stats = add_opening_lines(rows, leads, ctx, client(ai=True))
    assert stats.errors == 2 and stats.ai_lines == 1 and stats.template_lines == 2
    assert rows[0]["opening_line"] == template_line(rows[0])
    assert rows[1]["opening_line"] == template_line(rows[1])
    assert rows[2]["opening_line"] == GOOD
    # the HTTP error reported no usage -> not charged; the two answered calls cost 4 tokens each
    assert stats.cost_usd == pytest.approx(2 * 4 * 250 / 1e6)


def test_consecutive_failures_switch_ai_off(make_ctx):
    ctx = priced_ctx(make_ctx)
    llm = UsageLLM(LLMError("a"), LLMError("b"), LLMError("c"), GOOD)
    ctx.llm = llm
    rows, leads = rows_and_leads(5)
    stats = add_opening_lines(rows, leads, ctx, client(ai=True))
    assert len(llm.calls) == 3 and stats.errors == 3 and stats.template_lines == 5
    assert any("3 failures in a row" in n for n in stats.notes)


@pytest.mark.parametrize("exc", [LLMConfigError("model not set"), MissingCredentialError("no key")])
def test_permanent_error_switches_ai_off_at_once(make_ctx, exc):
    ctx = priced_ctx(make_ctx)
    llm = UsageLLM(exc, GOOD)
    ctx.llm = llm
    rows, leads = rows_and_leads(3)
    stats = add_opening_lines(rows, leads, ctx, client(ai=True))
    assert len(llm.calls) == 1 and stats.errors == 1 and stats.template_lines == 3
    assert any("configuration / key problem" in n for n in stats.notes)


def test_truncated_answer_is_charged_worst_case(make_ctx):
    ctx = priced_ctx(make_ctx)
    llm = UsageLLM(LLMTruncatedError("cut off"))
    ctx.llm = llm
    rows, leads = rows_and_leads(1)
    stats = add_opening_lines(rows, leads, ctx, client(ai=True))
    assert stats.errors == 1 and stats.cost_usd == pytest.approx(0.10)


def test_budget_exceeded_stops_ai(make_ctx):
    ctx = priced_ctx(make_ctx)
    llm = UsageLLM(BudgetExceeded("paid-lookup budget of 5 reached"), GOOD)
    ctx.llm = llm
    rows, leads = rows_and_leads(3)
    stats = add_opening_lines(rows, leads, ctx, client(ai=True))
    assert len(llm.calls) == 1 and stats.errors == 0 and stats.template_lines == 3
    assert any("budget" in n for n in stats.notes)


def test_llm_that_records_its_own_usage_is_not_double_counted(make_ctx):
    ctx = priced_ctx(make_ctx)

    class SelfMeteringLLM(UsageLLM):
        type_name = "anthropic"

        def complete(self, system, user, **kw):
            out = super().complete(system, user, **kw)
            ctx.usage.record_llm("llm", "anthropic", self.model, 100, 20)
            return out

    llm = SelfMeteringLLM(GOOD, GOOD, usage={"input_tokens": 100, "output_tokens": 20})
    ctx.llm = llm
    rows, leads = rows_and_leads(2)
    stats = add_opening_lines(rows, leads, ctx, client(ai=True))
    usage = {(r["kind"], r["type"]): r for r in ctx.usage.rows()}
    assert usage[("llm", "anthropic")]["output_tokens"] == 40          # recorded once per call
    assert stats.cost_usd == pytest.approx(2 * 20 * 250 / 1e6)


def test_client_settings_as_object_or_mapping(make_ctx):
    for cl in (SimpleNamespace(opening_line=SimpleNamespace(enabled=True, ai=True, max_cost_usd=1.0, max_tokens=None)),
               {"opening_line": {"ai": "true", "max_cost_usd": "1.0", "max_tokens": 120}}):
        ctx = priced_ctx(make_ctx)
        llm = UsageLLM(GOOD)
        ctx.llm = llm
        rows, leads = rows_and_leads(1)
        stats = add_opening_lines(rows, leads, ctx, cl)
        assert stats.ai_lines == 1
        expected = 120 if isinstance(cl, dict) else DEFAULT_MAX_TOKENS
        assert llm.calls[0]["max_tokens"] == expected


def test_last_usage_formats():
    from leadgen.delivery.opening import _usage_tokens
    assert _usage_tokens({"prompt_tokens": 10, "completion_tokens": 5}) == (10, 5)
    assert _usage_tokens({"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 90}) == (100, 5)
    assert _usage_tokens(SimpleNamespace(input_tokens=3, output_tokens=4)) == (3, 4)
    assert _usage_tokens({}) is None and _usage_tokens(None) is None
    assert _usage_tokens({"input_tokens": True}) is None


# --- the line talks about where the JOB is, not the company's HQ --------------------------

def _hq_lead(company_location: str, job_location: str, **kw: Any) -> Lead:
    lead = make_lead(location=job_location, **kw)
    lead.company.location = company_location
    return lead


@pytest.mark.parametrize("hq,job,expected", [
    # TheirStack: company.location is the HQ, the job is elsewhere
    ("San Francisco, United States", "Austin, TX",
     "Saw Acme Corp is hiring a Senior Accountant in Austin, TX (posted 2 days ago)."),
    # ATS boards: company.location is every job location joined
    ("New York, NY; Remote; Austin, TX", "Austin, TX",
     "Saw Acme Corp is hiring a Senior Accountant in Austin, TX (posted 2 days ago)."),
    # a remote-first company hiring on site: the role is not "remote"
    ("Remote", "Austin, TX", "Saw Acme Corp is hiring a Senior Accountant in Austin, TX (posted 2 days ago)."),
    # a remote job at an Austin company
    ("Austin, TX", "Remote - US", "Saw Acme Corp is hiring a remote Senior Accountant (posted 2 days ago)."),
    # the job's location is unknown: say nothing about where, never the HQ
    ("San Francisco, United States", "", "Saw Acme Corp is hiring a Senior Accountant (posted 2 days ago)."),
])
def test_opening_line_uses_the_job_location_not_the_company_location(make_ctx, hq, job, expected):
    lead = _hq_lead(hq, job)
    rows = [row_for(lead)]
    assert rows[0]["location"] == hq                     # the Location column stays company-level
    add_opening_lines(rows, {rows[0]["_lead_id"]: lead}, make_ctx(mode="delivery"), client(ai=False))
    assert rows[0]["opening_line"] == expected
    assert template_line(rows[0]) == expected             # reproducible from the row alone
    assert rows[0]["_job_location"] == job                # internal key, never written to client files


def test_ai_prompt_gets_the_job_location_not_the_company_hq(make_ctx):
    lead = _hq_lead("San Francisco, United States", "Austin, TX")
    row = row_for(lead)
    _, user = build_prompt(row, lead)
    assert "- Location: Austin, TX" in user and "San Francisco" not in user
    ctx = priced_ctx(make_ctx)
    llm = UsageLLM(GOOD, usage={"input_tokens": 10, "output_tokens": 10})
    ctx.llm = llm
    add_opening_lines([row], {row["_lead_id"]: lead}, ctx, client(ai=True))
    assert "- Location: Austin, TX" in llm.calls[0]["user"] and "San Francisco" not in llm.calls[0]["user"]
    # a non-hiring signal is about the company: its location is the company's
    funding = _hq_lead("San Francisco, United States", "Austin, TX", titles=["Raised $20M"], signal_type="funding")
    _, user = build_prompt(row_for(funding), funding)
    assert "- Location: San Francisco, United States" in user


# --- non-finite settings never switch the cost cap off or crash ----------------------------

@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "nan", "Infinity"])
def test_non_finite_cost_cap_falls_back_to_the_default_cap(make_ctx, bad):
    """max_cost_usd = NaN made every 'over the cap?' check False: no cap at all."""
    ctx = priced_ctx(make_ctx)
    llm = UsageLLM(*([GOOD] * 40), usage={"input_tokens": 500, "output_tokens": 400})   # $0.10 per call
    ctx.llm = llm
    rows, leads = rows_and_leads(40)
    stats = add_opening_lines(rows, leads, ctx, client(ai=True, max_cost=bad))
    assert stats.capped and stats.cost_usd <= 0.50 + 1e-9 and len(llm.calls) == 5
    assert any("max_cost_usd" in n for n in stats.notes)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "nan", 1e400, "lots"])
def test_non_finite_max_tokens_and_failure_limit_never_raise(make_ctx, bad):
    ctx = priced_ctx(make_ctx)
    ctx.playbook.writer["max_llm_failures"] = bad
    llm = UsageLLM(LLMError("a"), LLMError("b"), LLMError("c"), GOOD, usage={"input_tokens": 1, "output_tokens": 1})
    ctx.llm = llm
    rows, leads = rows_and_leads(4)
    stats = add_opening_lines(rows, leads, ctx, client(ai=True, max_tokens=bad))
    assert stats.template_lines == 4 and len(llm.calls) == 3            # default limit: 3 failures in a row
    assert all(c["max_tokens"] == DEFAULT_MAX_TOKENS for c in llm.calls)


def test_documented_client_settings_are_accepted_by_client_files():
    """Every opening_line key the module doc lists as a client setting must load from a client file."""
    import leadgen.delivery.opening as opening
    from leadgen.delivery.client import parse_client

    doc = opening.__doc__ or ""
    section = doc.split("Client settings read", 1)[1].split("Playbook keys read", 1)[0]
    keys = re.findall(r"^([a-z_]+)\s{2,}", section, re.M)
    assert {"enabled", "ai", "max_cost_usd"} <= set(keys)
    samples = {"enabled": True, "ai": True, "max_cost_usd": 0.5}
    for key in keys:
        parse_client({"opening_line": {key: samples.get(key, 1)}}, name="doc-check")
