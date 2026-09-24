"""Tests for leadgen.scoring: component maths, reasons and tiers."""
from __future__ import annotations

from datetime import timedelta

import pytest

from leadgen.models import Company, Contact, EmailStatus, Signal, Tier
from leadgen.scoring import (email_credit, freshness_credit, person_credit, score, tier_for,
                             volume_credit)
from tests.conftest import TODAY

BUYERS = {"titles": ["CFO", "VP Finance", "Finance Director", "Controller"]}
ICP = {"locations": ["United States"], "employees": {"min": 10, "max": 500}, "industries": ["software"]}


def job(title: str = "Accountant", age=2, **kw) -> Signal:
    return Signal(type="job_posting", title=title,
                  posted_at=TODAY - timedelta(days=age) if age is not None else None, **kw)


def hot_company() -> Company:
    return Company(
        name="Acme", domain="acme.com", location="Austin, TX", employees=120, industry="Software",
        signals=[
            job("Senior Accountant", 2),
            job("Controller - urgent", 9),
            job("AP Clerk", 25),
            Signal(type="funding", title="Series B", posted_at=TODAY - timedelta(days=10)),
            Signal(type="leadership_change", title="New CFO"),
        ])


def cfo(status: str = EmailStatus.VALID, email: str = "jane@acme.com", title: str = "CFO") -> Contact:
    return Contact(full_name="Jane Doe", title=title, email=email, email_status=status)


# --- helpers ------------------------------------------------------------------------------

@pytest.mark.parametrize("age,credit", [
    (0, 1.0), (3, 1.0), (4, 0.75), (7, 0.75), (8, 0.5), (14, 0.5), (15, 0.25), (30, 0.25),
    (31, 0.0), (400, 0.0), (None, 0.25),
])
def test_freshness_bands(age, credit):
    assert freshness_credit(age, [3, 7, 14, 30]) == credit


def test_freshness_custom_bands():
    assert freshness_credit(5, [10]) == 1.0 and freshness_credit(11, [10]) == 0.0
    assert freshness_credit(2, [1, 2]) == 0.5


@pytest.mark.parametrize("volume,full_at,credit", [
    (0, 3, 0.0), (1, 3, 0.0), (2, 3, 0.5), (3, 3, 1.0), (9, 3, 1.0), (3, 5, 0.5),
    (1, 1, 1.0), (0, 1, 0.0), (2, 0, 1.0), (2, "bad", 0.5),
])
def test_volume_credit(volume, full_at, credit):
    assert volume_credit(volume, full_at) == pytest.approx(credit)


@pytest.mark.parametrize("total,tier", [(100, Tier.HOT), (80, Tier.HOT), (79.9, Tier.NORMAL),
                                        (60, Tier.NORMAL), (59, Tier.SKIP), (0, Tier.SKIP)])
def test_tier_for_defaults(make_ctx, total, tier):
    assert tier_for(total, make_ctx()) == tier


def test_tier_for_custom(make_ctx):
    ctx = make_ctx(scoring={"tiers": {"hot": 50, "normal": 20}})
    assert tier_for(50, ctx) == "hot" and tier_for(20, ctx) == "normal" and tier_for(19, ctx) == "skip"


# --- full score ------------------------------------------------------------------------------

def test_perfect_lead_scores_100(make_ctx):
    ctx = make_ctx(icp=ICP, buyers=BUYERS)
    bd = score(hot_company(), cfo(), ctx)
    assert (bd.intent, bd.fit, bd.reachability, bd.extra) == (40, 30, 20, 10)
    assert bd.total == 100 and tier_for(bd.total, ctx) == "hot"
    for reason in ("fresh signal (2d)", "3 matching signals", "urgent language", "location match",
                   "size match", "industry match", "CFO = buyer #1", "email valid", "+funding",
                   "+leadership change"):
        assert reason in bd.reasons, reason
    assert "open 25d" in bd.reasons


def test_middling_lead_components(make_ctx):
    ctx = make_ctx(icp={"locations": ["United States"]}, buyers=BUYERS)
    company = Company(name="Beta", signals=[job("Accountant", 10)])
    bd = score(company, cfo(EmailStatus.RISKY, title="VP of Finance"), ctx)
    assert bd.intent == pytest.approx(7.5)       # fresh 15 * 0.5 of 40 sub-points * 40
    assert bd.fit == pytest.approx(25.0)         # (0.5 unknown + 1 + 1) / 3 * 30
    assert bd.reachability == pytest.approx(13)  # (0.8 + 0.5) / 2 * 20
    assert bd.extra == 0
    assert bd.total == int(round(7.5 + 25 + 13))
    assert "signal 10d old" in bd.reasons and "location unknown" in bd.reasons
    assert "VP of Finance = buyer #2" in bd.reasons and "email risky (catch-all)" in bd.reasons


def test_prescore_without_contact(make_ctx):
    ctx = make_ctx(buyers=BUYERS)
    bd = score(hot_company(), None, ctx)
    assert bd.reachability == 0 and "no decision-maker yet" in bd.reasons
    assert bd.total == 40 + 30 + 10


def test_no_primary_signal_means_no_intent(make_ctx):
    ctx = make_ctx()
    company = Company(name="Acme", signals=[Signal(type="funding", title="Seed", posted_at=TODAY)])
    bd = score(company, None, ctx)
    assert bd.intent == 0 and "no primary signal" in bd.reasons
    assert bd.extra == 5 and "+funding" in bd.reasons


def test_repost_reason_and_persistence_points(make_ctx):
    ctx = make_ctx()
    sig = job("Accountant", 2)
    sig.reposted = True
    sig.first_seen = TODAY - timedelta(days=28)
    bd = score(Company(name="Acme", signals=[sig]), None, ctx)
    assert "reposted / open 28d" in bd.reasons
    assert bd.intent == pytest.approx(15 + 10)   # fresh + persistence


def test_undated_signal_gets_quarter_fresh_credit(make_ctx):
    ctx = make_ctx()
    bd = score(Company(name="Acme", signals=[job("Accountant", None)]), None, ctx)
    assert bd.intent == pytest.approx(15 * 0.25) and "signal date unknown" in bd.reasons


def test_old_signal_reason(make_ctx):
    ctx = make_ctx()
    bd = score(Company(name="Acme", signals=[job("Accountant", 45)]), None, ctx)
    assert "old signal (45d)" in bd.reasons
    assert bd.intent == pytest.approx(10)        # only persistence (45 >= 21 stale days)


def test_custom_intent_subpoints_and_weights(make_ctx):
    ctx = make_ctx(scoring={"weights": {"intent": 60, "fit": 20, "reachability": 20, "extra": 0},
                            "intent": {"fresh": 1, "volume": 0, "persistence": 0, "urgency": 1}})
    company = Company(name="Acme", signals=[job("Accountant - ASAP", 1)])
    bd = score(company, None, ctx)
    assert bd.intent == pytest.approx(60) and bd.extra == 0


def test_zero_intent_config_is_safe(make_ctx):
    ctx = make_ctx(scoring={"intent": {"fresh": 0, "volume": 0, "persistence": 0, "urgency": 0}})
    bd = score(Company(name="Acme", signals=[job()]), None, ctx)
    assert bd.intent == 0


def test_fit_unknown_credit(make_ctx):
    ctx = make_ctx(icp=ICP, scoring={"unknown_credit": 0})
    bd = score(Company(name="Acme"), None, ctx)
    assert bd.fit == 0 and {"location unknown", "size unknown", "industry unknown"} <= set(bd.reasons)
    ctx2 = make_ctx(icp=ICP, scoring={"unknown_credit": 1})
    assert score(Company(name="Acme"), None, ctx2).fit == 30


def test_fit_mismatch_and_unconfigured(make_ctx):
    ctx = make_ctx(icp={"employees": {"min": 1000}})
    bd = score(Company(name="Acme", employees=20), None, ctx)
    assert bd.fit == pytest.approx(20) and "size mismatch" in bd.reasons
    assert "location match" not in bd.reasons  # unconfigured criteria earn points silently


@pytest.mark.parametrize("title,credit", [
    ("CFO", 1.0), ("Chief Financial Officer", 1.0), ("VP Finance", 0.8), ("Finance Director", 0.8),
    ("Financial Controller", 0.6), ("Sales Manager", 0.3), ("", 0.3), ("Finance Intern", 0.0),
])
def test_person_credit(make_ctx, title, credit):
    ctx = make_ctx(buyers=BUYERS)
    assert person_credit(Contact(full_name="Jo Bloggs", title=title), ctx)[0] == credit


def test_person_credit_without_buyer_titles(make_ctx):
    ctx = make_ctx()
    assert person_credit(Contact(full_name="Jo Bloggs", title="Anything"), ctx) == (0.8, "Anything found")
    assert person_credit(Contact(full_name="Jo Bloggs", title="Summer Intern"), ctx)[0] == 0.0
    assert person_credit(None, ctx) == (0.0, "no decision-maker yet")


@pytest.mark.parametrize("email,status,credit,reason", [
    ("a@x.com", EmailStatus.VALID, 1.0, "email valid"),
    ("a@x.com", EmailStatus.RISKY, 0.5, "email risky (catch-all)"),
    ("a@x.com", EmailStatus.UNKNOWN, 0.3, "email unverified"),
    ("a@x.com", EmailStatus.INVALID, 0.0, "email invalid"),
    ("", EmailStatus.VALID, 0.0, "no email"),
])
def test_email_credit(email, status, credit, reason):
    assert email_credit(Contact(full_name="A B", email=email, email_status=status)) == (credit, reason)


def test_email_credit_candidates_only_is_zero():
    assert email_credit(Contact(full_name="A B", email_candidates=["a@x.com"]))[0] == 0.0


def test_extra_is_capped_by_weight(make_ctx):
    types = ["funding", "expansion", "leadership_change"]
    company = Company(name="Acme", signals=[Signal(type=t, title=t) for t in types])
    assert score(company, None, make_ctx()).extra == 10
    assert score(company, None, make_ctx(scoring={"extra_per_signal": 2})).extra == 6
    # duplicate types count once
    dup = Company(name="Acme", signals=[Signal(type="funding", title="A"), Signal(type="funding", title="B")])
    assert score(dup, None, make_ctx()).extra == 5


def test_total_is_clamped(make_ctx):
    ctx = make_ctx(scoring={"weights": {"intent": 90, "fit": 90, "reachability": 20, "extra": 10}})
    bd = score(hot_company(), cfo(), ctx)
    assert bd.total == 100


def test_breakdown_serialises(make_ctx):
    bd = score(hot_company(), cfo(), make_ctx(buyers=BUYERS))
    d = bd.to_dict()
    assert d["total"] == bd.total and isinstance(d["reasons"], list)
    assert all(isinstance(r, str) and r for r in bd.reasons)


def test_fresh_repost_does_not_show_trivial_open_days(make_ctx):
    ctx = make_ctx()
    sig = job("Accountant", 1)
    sig.reposted = True
    sig.first_seen = TODAY - timedelta(days=2)
    bd = score(Company(name="Acme", signals=[sig]), None, ctx)
    assert "reposted" in bd.reasons and not any(r.startswith("reposted / open") for r in bd.reasons)
