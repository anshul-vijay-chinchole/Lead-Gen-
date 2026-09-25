"""Tests for leadgen.delivery.qa: reason grouping, the counts, warnings, lines and JSON."""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from leadgen.delivery.client import Client
from leadgen.delivery.opening import OpeningStats
from leadgen.delivery.qa import (KIND_DUPLICATE, KIND_FILTERED, KIND_OVER_LIMIT, TOP_REASONS, QAReport, build_qa,
                                 group_reason, top_reasons)
from leadgen.delivery.rows import EMAIL_LABELS, DeliveryPackage, build_row
from leadgen.models import Company, Contact, EmailStatus, Lead, Signal
from leadgen.pipeline import RunResult
from leadgen.usage import BudgetExceeded, UsageMeter
from tests.conftest import TODAY


# --- builders ---------------------------------------------------------------------------

def make_lead(name: str, *, email: str = "", status: str = EmailStatus.VALID, guessed: bool = False,
              tier: str = "normal", score: int = 60, contact: bool = True) -> Lead:
    domain = name.lower().replace(" ", "-") + ".example"
    company = Company(name=name, domain=domain, location="Austin, TX", employees=120,
                      signals=[Signal(type="job_posting", title="Senior Accountant", posted_at=TODAY - timedelta(days=2),
                                      url=f"https://{domain}/jobs/1", source="csv")])
    ct = None
    if contact:
        ct = Contact(full_name="Jane Doe", title="CFO", email=email or f"jane@{domain}", email_status=status,
                     source="csv")
        if guessed:
            ct.data["email_guessed"] = True
    return Lead(company=company, contact=ct, score=score, tier=tier, playbook="client-acme")


def make_pkg(leads: List[Lead], include_unverified: bool = True) -> DeliveryPackage:
    rows = [build_row(ld, TODAY, include_unverified=include_unverified) for ld in leads]
    return DeliveryPackage(client_name="acme", client_display="Acme Staffing Ltd",
                           period_start=TODAY - timedelta(days=7), period_end=TODAY, rows=rows, run_id="run-1")


def make_run(*, rejected: Optional[List[Dict[str, str]]] = None, errors: Optional[List[str]] = None,
             warnings: Optional[List[str]] = None, usage: Optional[List[str]] = None,
             counts: Optional[Dict[str, int]] = None) -> RunResult:
    return RunResult(run_id="run-1", playbook="client-acme", out_dir=Path("deliveries/acme/_internal/run-1"),
                     counts=counts if counts is not None else {"sourced": 40, "with_signal": 12, "qualified": 8},
                     rejected=list(rejected or []), errors=list(errors or []), warnings=list(warnings or []),
                     usage=list(usage or []))


def reject(company: str, reason: str, stage: str = "icp") -> Dict[str, str]:
    return {"company": company, "domain": "", "stage": stage, "reason": reason}


def client(**kw: Any) -> Client:
    kw.setdefault("leads_per_week", 3)
    return Client("acme", display_name="Acme Staffing Ltd", **kw)


FULL = [make_lead("Acme Corp", tier="hot", score=85),
        make_lead("Beta LLC", status=EmailStatus.RISKY),
        make_lead("Gamma Inc", guessed=True)]


# --- reason grouping -----------------------------------------------------------------------

@pytest.mark.parametrize("reason, grouped", [
    ("too small (12 employees, min 20)", "too small"),
    ("too large (5000 employees, max 1000)", "too large"),
    ("no signal matching [accountant, controller] in last 7 days (3 signals found: 2 not matching; "
     "1 older than 7 days)", "no signal matching [...] in last 7 days"),
    ("no signal matching [accountant] in last 30 days (1 signal found: 1 re-posted (stale listing))",
     "no signal matching [...] in last 30 days"),
    ("already delivered to this client (2026-09-17)", "already delivered to this client"),
    ("already delivered to this client 2026-09-17", "already delivered to this client"),
    ("on this client's do-not-list (domain)", "on this client's do-not-list"),
    ("location not in ICP [Texas, Oklahoma] (got: Denver, CO)", "location not in ICP [...]"),
    ("domain acme.com is on the suppression list", "domain is on the suppression list"),
    ("company Acme Holdings Inc is on the suppression list", "company is on the suppression list"),
    ("excluded domain eu.bigclient.com (under bigclient.com)", "excluded domain"),
    ("company name 'Acme Staffing' matches excluded pattern 'staffing'",
     "company name matches an excluded pattern"),
    ("excluded keyword 'staffing' in name", "excluded keyword 'staffing' in name"),
    ("size unknown (icp.unknown_passes is off)", "size unknown"),
    ("urgency 'skip' is not delivered to this client (tiers: hot, normal)",
     "urgency 'skip' is not delivered to this client"),
    ("3 re-posted listings", "re-posted listings"),
    ("scored 12.5 points below 20", "scored points below"),
    ("all its jobs were already delivered", "all its jobs were already delivered"),
    ("a (b (c) d) e", "a e"),
    ("  lots   of\n spaces  ", "lots of spaces"),
    ("(only details)", "other"),
    ("", "other"),
    (None, "other"),
])
def test_group_reason(reason, grouped):
    assert group_reason(reason) == grouped


def test_top_reasons_counts_groups_most_common_first_then_alphabetical():
    reasons = ["too small (5 employees, min 20)", "too small (12 employees, min 20)",
               "already delivered to this client (2026-09-17)", "already delivered to this client (2026-09-10)",
               "excluded keyword 'staffing' in name", "b-reason", "a-reason", "too small (1 employees, min 20)"]
    assert top_reasons(reasons) == [("too small", 3), ("already delivered to this client", 2), ("a-reason", 1),
                                    ("b-reason", 1), ("excluded keyword 'staffing' in name", 1)]
    assert top_reasons(reasons, n=1) == [("too small", 3)]
    assert top_reasons([], n=3) == [] and top_reasons(["x"], n=0) == []
    assert TOP_REASONS == 5


# --- the counts ------------------------------------------------------------------------------

def test_build_qa_counts_everything():
    run = make_run(rejected=[
        reject("Tiny Co", "too small (5 employees, min 20)"),
        reject("Small Co", "too small (12 employees, min 20)"),
        reject("Old Co", "no signal matching [accountant] in last 7 days (1 signal found: 1 older than 7 days)",
               stage="signals"),
        reject("Known Co", "already delivered to this client (2026-09-17)", stage="delivery"),
        reject("Their Client", "on this client's do-not-list (company name)", stage="delivery"),
        reject("Blocked Co", "domain blocked.example is on the suppression list"),
    ])
    selection = [
        {"company": "Funded Co", "reason": "no live job posting (only other signals, e.g. funding news)",
         "kind": KIND_FILTERED},
        {"company": "Cold Co", "reason": "urgency 'skip' is not delivered to this client (tiers: hot, normal)",
         "kind": KIND_FILTERED},
        {"company": "Acme Corp", "reason": "same company as a higher-ranked lead in this delivery",
         "kind": KIND_DUPLICATE},
        {"company": "Extra Co", "reason": "over this client's weekly limit of 3 leads", "kind": KIND_OVER_LIMIT},
    ]
    qa = build_qa(client(), run, make_pkg(FULL), {"company": 1, "job": 4, "contact": 2, "suppressed": 1},
                  {"company": 1, "contact": 0}, not_delivered=selection)

    assert isinstance(qa, QAReport)
    assert (qa.client, qa.client_display, qa.date, qa.run_id) == ("acme", "Acme Staffing Ltd", "2026-09-24", "run-1")
    assert (qa.leads_found, qa.with_signal, qa.qualified) == (40, 12, 8)
    assert (qa.delivered, qa.target, qa.hot, qa.companies) == (3, 3, 1, 3)
    assert qa.filtered_out == 6 + 2                  # every pipeline rejection + the selection filters
    assert qa.top_reasons[0] == ("too small", 2)
    assert ("no live job posting", 1) in qa.top_reasons
    assert qa.duplicates_removed == 1 + 4 + 2 + 1    # ledger company / job / contact + same company in run
    assert qa.duplicates == {"company": 1, "job": 4, "contact": 2, "same_company_in_run": 1}
    assert qa.suppressed == 1 + 1                     # client's do-not-list + the global suppression list
    assert qa.held_back == 1
    assert qa.email_status_counts == {"verified": 1, "risky": 1, "guessed-unverified": 1, "not found": 0}
    assert qa.verified_email_rate == pytest.approx(1 / 3)
    assert qa.warnings == []                          # delivered == target, nothing went wrong
    assert not qa.below_target


def test_verified_rate_respects_the_email_policy_and_zero_rows():
    qa = build_qa(client(), make_run(), make_pkg(FULL, include_unverified=False))
    assert qa.email_status_counts == {"verified": 1, "risky": 0, "guessed-unverified": 0, "not found": 2}
    assert qa.verified_email_rate == pytest.approx(1 / 3)
    empty = build_qa(client(), make_run(), make_pkg([]))
    assert empty.verified_email_rate == 0.0 and empty.delivered == 0 and empty.companies == 0
    assert set(empty.email_status_counts) == set(EMAIL_LABELS)


def test_a_guessed_email_the_verifier_called_valid_is_not_counted_as_verified():
    qa = build_qa(client(leads_per_week=1), make_run(), make_pkg([make_lead("Gamma Inc", guessed=True)]))
    assert qa.email_status_counts["verified"] == 0 and qa.email_status_counts["guessed-unverified"] == 1
    assert qa.verified_email_rate == 0.0


def test_within_run_dupes_may_be_an_int_and_ledger_removed_may_be_missing():
    qa = build_qa(client(), make_run(), make_pkg(FULL), None, 2)
    assert qa.duplicates == {"in_run": 2} and qa.duplicates_removed == 2 and qa.suppressed == 0
    qa2 = build_qa(client(), make_run(), make_pkg(FULL), {}, {"contact": 1})
    assert qa2.duplicates == {"same_contact_in_run": 1}
    qa3 = build_qa(client(), make_run(), make_pkg(FULL))
    assert qa3.duplicates == {} and qa3.duplicates_removed == 0


def test_client_can_be_a_plain_object_or_name():
    qa = build_qa(SimpleNamespace(name="beta", leads_per_week=5), make_run(), make_pkg(FULL))
    assert qa.client == "beta" and qa.client_display == "beta" and qa.target == 5
    qa2 = build_qa("gamma", make_run(), make_pkg(FULL))
    assert qa2.client == "gamma" and qa2.target == 0 and not qa2.below_target


# --- warnings ----------------------------------------------------------------------------------

def test_zero_leads_warns_twice_empty_files_and_low_volume():
    qa = build_qa(client(leads_per_week=25), make_run(), make_pkg([]))
    assert qa.below_target
    assert any(w.startswith("No leads in this delivery") and "Don't send" in w for w in qa.warnings)
    assert any(w.startswith("Low volume: 0 of 25 leads delivered") for w in qa.warnings)


def test_low_volume_warning_and_its_hint():
    qa = build_qa(client(leads_per_week=10), make_run(), make_pkg(FULL))
    low = [w for w in qa.warnings if w.startswith("Low volume")]
    assert low == [low[0]] and "3 of 10" in low[0] and "widen the roles" in low[0]
    assert "already delivered" not in low[0]
    # most qualified companies were removed by the ledger -> say so
    dup = build_qa(client(leads_per_week=10), make_run(counts={"sourced": 9, "with_signal": 6, "qualified": 5}),
                   make_pkg(FULL[:1]), {"company": 4, "job": 0, "contact": 0, "suppressed": 0})
    assert any("already delivered to this client in earlier weeks" in w for w in dup.warnings)


def test_budget_warning_from_the_meter():
    meter = UsageMeter(max_paid_lookups=2)
    for _ in range(2):
        meter.before_request("verifier", "millionverifier", True)
    with pytest.raises(BudgetExceeded):
        meter.before_request("verifier", "millionverifier", True)
    qa = build_qa(client(), make_run(), make_pkg(FULL), usage=meter)
    budget = [w for w in qa.warnings if "budget" in w]
    assert len(budget) == 1 and "Paid-lookup budget reached (2 of 2 paid lookups used)" in budget[0]
    assert "--budget N" in budget[0]
    assert (qa.paid_lookups, qa.max_paid_lookups) == (2, 2)
    assert qa.usage_lines[0] == "API usage: paid lookups 2/2"
    assert any("1 skipped by budget" in u for u in qa.usage_lines)


def test_budget_warning_from_pipeline_warnings_is_not_repeated_raw():
    run = make_run(warnings=["paid-lookup budget of 3 reached: no more contact lookups this run",
                             "paid-lookup budget reached: remaining emails checked with the free basic checker",
                             "exporter 'x' skipped (delivery mode)"])
    qa = build_qa(client(), run, make_pkg(FULL))
    assert sum("budget" in w for w in qa.warnings) == 1
    assert "exporter 'x' skipped (delivery mode)" in qa.warnings     # other pipeline warnings pass through


def test_budget_used_up_exactly_is_a_note_not_a_warning():
    meter = UsageMeter(max_paid_lookups=1)
    meter.before_request("finder", "hunter", True)
    qa = build_qa(client(), make_run(), make_pkg(FULL), usage=meter)
    assert not any("budget" in w for w in qa.warnings)
    assert "all 1 paid lookups of the budget were used" in qa.notes


def test_source_errors_and_other_errors():
    errors = [f"source feed{i}: HTTP 500 from https://api.example/jobs" for i in range(7)]
    errors += ["enrich Acme Corp: boom", "verify x@y.example: timeout"]
    qa = build_qa(client(), make_run(errors=errors), make_pkg(FULL))
    src = [w for w in qa.warnings if w.startswith("Source problem")]
    assert len(src) == 5 and "source feed0: HTTP 500" in src[0] and "missing this week" in src[0]
    assert "... and 2 more source problem(s)." in qa.warnings
    other = [w for w in qa.warnings if "other error(s)" in w]
    assert other and other[0].startswith("2 other error(s) during the run, e.g. enrich Acme Corp: boom")
    assert "summary.json" in other[0]


def test_ai_cost_cap_warning():
    stats = OpeningStats(ai_lines=2, template_lines=1, cost_usd=0.0049, capped=True,
                         notes=["AI cost cap of $0.01 reached after 2 AI line(s) - template lines used for the rest"])
    qa = build_qa(client(opening_line={"enabled": True, "ai": True, "max_cost_usd": 0.005}), make_run(),
                  make_pkg(FULL), opening_stats=stats)
    cap = [w for w in qa.warnings if "cost cap" in w]
    assert cap == [cap[0]] and "($0.005)" in cap[0] and "1 line(s) use the free template" in cap[0]
    assert not any("cost cap" in n for n in qa.notes)                  # said once, as a warning
    assert qa.opening == {"ai_lines": 2, "template_lines": 1, "cost_usd": 0.0049, "capped": True, "errors": 0,
                          "notes": stats.notes}
    line = [ln for ln in qa.lines() if "opening lines" in ln][0]
    assert "2 AI, 1 template, ~$0.0049" in line

    fifty = build_qa(client(opening_line={"enabled": True, "ai": True, "max_cost_usd": 0.5}), make_run(),
                     make_pkg(FULL), opening_stats=OpeningStats(template_lines=3, capped=True))
    assert any("($0.50)" in w for w in fifty.warnings)


def test_zero_ai_budget_is_a_choice_not_a_warning():
    stats = OpeningStats(template_lines=3, capped=True,
                         notes=["opening_line.max_cost_usd is 0, so no AI spend is allowed - template lines used"])
    qa = build_qa(client(opening_line={"enabled": True, "ai": True, "max_cost_usd": 0}), make_run(),
                  make_pkg(FULL), opening_stats=stats)
    assert not any("cost cap" in w for w in qa.warnings)
    assert stats.notes[0] in qa.notes


def test_extra_warnings_are_added_once_in_order():
    qa = build_qa(client(leads_per_week=4), make_run(warnings=["same thing"]), make_pkg(FULL),
                  extra_warnings=["Google Sheet not updated: boom", "", "same thing"])
    assert qa.warnings[0].startswith("Low volume")
    assert qa.warnings[1:] == ["Google Sheet not updated: boom", "same thing"]


# --- usage ----------------------------------------------------------------------------------------

def test_usage_lines_and_estimated_cost_from_the_meter():
    meter = UsageMeter(cost_per_call={"hunter": 0.01})
    for _ in range(3):
        meter.before_request("finder", "hunter", True)
    meter.before_request("source", "csv", False)
    qa = build_qa(client(), make_run(), make_pkg(FULL), usage=meter)
    assert qa.paid_lookups == 3 and qa.max_paid_lookups == 0
    assert qa.estimated_cost_usd == pytest.approx(0.03)
    assert qa.usage_lines[0] == "API usage: paid lookups 3 (no cap)"
    assert "  estimated cost: ~$0.0300" in qa.usage_lines
    text = qa.text()
    assert "API usage: paid lookups 3 (no cap)" in text and "finder hunter (paid): 3 calls" in text


def test_usage_falls_back_to_the_run_usage_lines():
    run = make_run(usage=["API usage: paid lookups 4/10", "  finder apollo (paid): 4 calls"],
                   counts={"sourced": 1, "with_signal": 1, "qualified": 1, "paid_lookups": 4})
    qa = build_qa(client(), run, make_pkg(FULL))
    assert qa.usage_lines == run.usage and qa.paid_lookups == 4


# --- lines + JSON ------------------------------------------------------------------------------------

def test_lines_show_the_numbers_reasons_and_warnings():
    run = make_run(rejected=[reject("Tiny", "too small (5 employees, min 20)")])
    qa = build_qa(client(leads_per_week=5), run, make_pkg(FULL), {"company": 2, "job": 1, "contact": 0,
                                                                   "suppressed": 0}, {"contact": 1},
                  not_delivered=[{"company": "X", "reason": "over the limit", "kind": KIND_OVER_LIMIT}],
                  notes=["Google Sheet not updated (dry run)"])
    qa.folder, qa.files = "deliveries/acme/2026-09-24", {"csv": "deliveries/acme/2026-09-24/a.csv"}
    qa.sheet_url = "https://docs.google.com/spreadsheets/d/abc"
    lines = qa.lines()
    assert lines[0] == "Delivery QA - Acme Staffing Ltd (acme) - 2026-09-24"
    text = "\n".join(lines)
    assert "companies found ............ 40" in text
    assert "delivered .................. 3 (target 5), 1 hot" in text
    assert "filtered out ............... 1" in text and "1  too small" in text
    assert "duplicates removed ......... 4 (already delivered: 2 companies, 1 job; repeated in this run: 1)" in text
    assert "held back (over the limit) . 1" in text
    assert "verified email rate ........ 33% (1 of 3)" in text
    assert "email status ............... verified 1, risky 1, guessed-unverified 1, not found 0" in text
    assert "files (deliveries/acme/2026-09-24):" in text and "csv   deliveries/acme/2026-09-24/a.csv" in text
    assert "google sheet: https://docs.google.com/spreadsheets/d/abc" in text
    assert "note: Google Sheet not updated (dry run)" in text
    assert "WARNING: Low volume: 3 of 5" in text
    assert qa.text() == text
    assert "opening lines" not in text                # the column is off: no opening line summary


def test_dry_run_lines_and_no_leads_rate():
    qa = build_qa(client(), make_run(), make_pkg([]), dry_run=True)
    assert qa.dry_run and qa.lines()[0].endswith("- DRY RUN (preview only)")
    assert "nothing was recorded as delivered" in qa.lines()[1]
    assert "verified email rate ........ n/a (no leads)" in qa.text()


def test_to_dict_is_json_ready_and_complete():
    run = make_run(rejected=[reject("Tiny", "too small (5 employees, min 20)")])
    qa = build_qa(client(), run, make_pkg(FULL), {"company": 1}, 0, OpeningStats(template_lines=3),
                  UsageMeter(max_paid_lookups=5), dry_run=True)
    d = json.loads(json.dumps(qa.to_dict()))
    for key in ("client", "leads_found", "with_signal", "qualified", "delivered", "target", "filtered_out",
                "top_reasons", "duplicates_removed", "suppressed", "verified_email_rate", "email_status_counts",
                "warnings", "usage_lines"):
        assert key in d, key
    assert d["top_reasons"] == [["too small", 1]]
    assert d["dry_run"] is True and d["below_target"] is False and d["max_paid_lookups"] == 5
    assert d["opening"]["template_lines"] == 3
    assert d["verified_email_rate"] == pytest.approx(0.3333)
