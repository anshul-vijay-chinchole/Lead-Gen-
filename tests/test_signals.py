"""Tests for leadgen.signals: keyword matching, the signal stage and signal stats."""
from __future__ import annotations

import dataclasses
from datetime import date, timedelta

import pytest

from leadgen.models import Company, Signal
from leadgen.signals import (as_str_list, contains_tokens, keyword_match, process_signals,
                             signal_stats, tokenize, tokens_equal)
from tests.conftest import TODAY


def days_ago(n: int) -> date:
    return TODAY - timedelta(days=n)


def job(title: str, age: int = None, **kw) -> Signal:
    return Signal(type="job_posting", title=title, posted_at=days_ago(age) if age is not None else None, **kw)


# --- keyword_match ------------------------------------------------------------------

class TestKeywordMatch:
    def test_plural_tolerance_both_ways(self):
        assert keyword_match("Senior Accountants wanted", ["accountant"]) == "accountant"
        assert keyword_match("Software Engineers (x3)", ["engineer"]) == "engineer"
        assert keyword_match("Staff Accountant", ["accountants"]) == "accountants"
        assert keyword_match("Tax Advisors & taxes", ["tax"]) == "tax"
        assert keyword_match("Growing companies", ["company"]) == "company"
        assert keyword_match("One company", ["companies"]) == "companies"

    def test_never_inside_other_words(self):
        assert keyword_match("Director of Sales", ["cto"]) is None
        assert keyword_match("three open roles", ["hr"]) is None
        assert keyword_match("Internal Audit Manager", ["intern"]) is None
        assert keyword_match("Houston office", ["us"]) is None
        assert keyword_match("Accounting Manager", ["accountant"]) is None

    def test_short_tokens_need_exact_match(self):
        assert keyword_match("its a match", ["it"]) is None
        assert keyword_match("IT Manager", ["it"]) == "it"
        assert keyword_match("HR Business Partner", ["HR"]) == "HR"

    def test_case_accents_punctuation(self):
        assert keyword_match("CAFÉ MANAGER", ["cafe"]) == "cafe"
        assert keyword_match("Cafe manager", ["Café"]) == "Café"
        assert keyword_match("Head of Finance, EMEA", ["head of finance"]) == "head of finance"
        assert keyword_match("Front-end developer", ["front end"]) == "front end"

    def test_phrases_are_whole_and_ordered(self):
        assert keyword_match("Financial Controllers needed", ["financial controller"]) == "financial controller"
        assert keyword_match("Controller, Financial Planning", ["financial controller"]) is None
        assert keyword_match("start now please", ["start now"]) == "start now"

    def test_compound_spellings(self):
        assert keyword_match("E-commerce Manager", ["ecommerce"]) == "ecommerce"
        assert keyword_match("Ecommerce Manager", ["e-commerce"]) == "e-commerce"
        assert keyword_match("Co-Founder", ["cofounder"]) == "cofounder"
        assert keyword_match("Healthcare provider", ["health care"]) == "health care"
        # short compounds are not joined (keeps "u s" from matching "us" everywhere)
        assert keyword_match("u s", ["us"]) is None

    def test_returns_first_keyword_in_given_order(self):
        assert keyword_match("Accountant and Controller", ["controller", "accountant"]) == "controller"

    def test_degenerate_inputs(self):
        assert keyword_match(None, ["x"]) is None
        assert keyword_match("", ["x"]) is None
        assert keyword_match("anything", []) is None
        assert keyword_match("anything", None) is None
        assert keyword_match("anything", ["", None, "   "]) is None
        assert keyword_match("!!!", ["x"]) is None

    def test_single_string_keyword_is_not_iterated_by_char(self):
        assert keyword_match("Accountant", "accountant") == "accountant"
        assert keyword_match("a b c", "abc") is None

    def test_list_text_checked_item_by_item(self):
        assert keyword_match(["saas", "fintech"], ["fintech"]) == "fintech"
        # words from different items are never glued into a phrase
        assert keyword_match(["finance", "director"], ["finance director"]) is None

    def test_non_string_keywords(self):
        assert keyword_match("Top 500 company", [500]) == "500"


def test_token_helpers():
    assert tokenize("Hello, Wörld!") == ["hello", "world"]
    assert tokens_equal("city", "cities") and tokens_equal("box", "boxes")
    assert not tokens_equal("it", "its") and not tokens_equal("cto", "director")
    assert contains_tokens(["a", "big", "dog"], ["big", "dogs"])
    assert not contains_tokens([], ["x"]) and not contains_tokens(["x"], [])
    assert as_str_list(None) == [] and as_str_list("x") == ["x"] and as_str_list(["a", None, " ", 3]) == ["a", "3"]
    assert as_str_list("  ") == []


# --- process_signals ------------------------------------------------------------------

def test_types_filter_and_primary_only_match_keywords(make_ctx):
    ctx = make_ctx(signals={"types": ["job_posting", "funding"], "match_keywords": ["accountant"]})
    c = Company(name="Acme", domain="acme.com", signals=[
        job("Senior Accountant", 3),
        job("Marketing Manager", 2),                                  # primary, no keyword -> dropped
        Signal(type="funding", title="Series A", posted_at=days_ago(10)),  # secondary: no keyword needed
        Signal(type="news", title="Accountant award", posted_at=days_ago(1)),  # type not accepted
    ])
    kept, rejected = process_signals([c], ctx)
    assert kept == [c] and rejected == []
    assert [s.title for s in c.signals] == ["Senior Accountant", "Series A"]


def test_empty_types_accepts_everything(make_ctx):
    ctx = make_ctx(signals={"types": []})
    c = Company(name="Acme", signals=[Signal(type="custom", title="Anything", posted_at=days_ago(1))])
    kept, _ = process_signals([c], ctx)
    assert kept and c.signals[0].type == "custom"


def test_type_matching_is_case_insensitive(make_ctx):
    ctx = make_ctx(signals={"types": ["Job_Posting"], "primary": ["JOB_POSTING"]})
    c = Company(name="Acme", signals=[Signal(type="job_posting", title="Accountant", posted_at=days_ago(1))])
    kept, _ = process_signals([c], ctx)
    assert kept and signal_stats(c, ctx)["volume"] == 1


def test_exclude_keywords_drop_signal_titles(make_ctx):
    ctx = make_ctx(signals={"exclude_keywords": ["intern", "graduate"]})
    c = Company(name="Acme", signals=[job("Finance Intern", 1), job("Graduate Accountants", 1),
                                      job("Internal Auditor", 1)])
    process_signals([c], ctx)
    assert [s.title for s in c.signals] == ["Internal Auditor"]


def test_match_keywords_description_fallback(make_ctx):
    ctx = make_ctx(signals={"match_keywords": ["controller"]})
    c = Company(name="Acme", signals=[
        job("Finance Lead", 2, description="You will act as our financial controller."),
        job("Office Manager", 1, description="Keep the office running."),
    ])
    process_signals([c], ctx)
    assert [s.title for s in c.signals] == ["Finance Lead"]


def test_observe_sets_first_seen_and_keeps_unseen_undated(make_ctx):
    ctx = make_ctx()
    undated = job("Accountant")
    c = Company(name="Acme", domain="acme.com", signals=[undated])
    kept, rejected = process_signals([c], ctx)
    assert kept == [c] and not rejected
    assert undated.first_seen == TODAY and undated.age_days(TODAY) == 0


def test_undated_signal_seen_long_ago_is_dropped(make_ctx):
    ctx = make_ctx(signals={"max_age_days": 30})
    ctx.store.observe_signals("acme.com", [job("Accountant")], days_ago(90))
    c = Company(name="Acme", domain="acme.com", signals=[job("Accountant")])
    kept, rejected = process_signals([c], ctx)
    assert kept == [] and len(rejected) == 1
    assert "older than 30 days" in rejected[0][1]


def test_repost_detected_across_runs(make_ctx):
    ctx = make_ctx()
    first = Company(name="Acme", domain="acme.com",
                    signals=[job("Accountant", 20, external_id="a1")])
    process_signals([first], ctx)
    assert not first.signals[0].reposted
    again = Company(name="Acme", domain="acme.com",
                    signals=[job("Accountant", 1, external_id="a2")])
    process_signals([again], ctx)
    assert again.signals[0].reposted
    assert signal_stats(again, ctx)["persistent"] is True


def test_max_age_drops_old_and_can_be_disabled(make_ctx):
    ctx = make_ctx(signals={"max_age_days": 60})
    c = Company(name="Acme", signals=[job("Accountant", 61), job("Controller", 60)])
    process_signals([c], ctx)
    assert [s.title for s in c.signals] == ["Controller"]

    ctx2 = make_ctx(signals={"max_age_days": None})
    c2 = Company(name="Acme", signals=[job("Accountant", 400)])
    kept, _ = process_signals([c2], ctx2)
    assert kept and len(c2.signals) == 1


def test_sort_primary_first_then_freshest_undated_last(make_ctx):
    ctx = make_ctx(signals={"max_age_days": 100})
    c = Company(name="Acme", signals=[
        Signal(type="funding", title="Seed", posted_at=days_ago(1)),
        job("B", 10),
        job("C", 2),
        Signal(type="news", title="Press"),
        job("A", 5),
    ])
    # without a store undated signals stay undated (no first_seen) and sort last
    process_signals([c], dataclasses.replace(ctx, store=None))
    assert [s.title for s in c.signals] == ["C", "A", "B", "Seed", "Press"]


def test_require_rejects_with_readable_reason(make_ctx):
    ctx = make_ctx(signals={"match_keywords": ["accountant", "controller"], "exclude_keywords": ["intern"]})
    c = Company(name="Acme", signals=[job("Sales Rep", 1), job("Accountant Intern", 2), job("Accountant", 90)])
    kept, rejected = process_signals([c], ctx)
    assert kept == []
    (co, reason), = rejected
    assert co is c and c.signals == []
    assert reason.startswith("no signal matching [accountant, controller] in last 60 days")
    assert "3 signals found" in reason
    assert "1 not matching" in reason and "1 excluded by keyword (intern)" in reason
    assert "1 older than 60 days" in reason


def test_reason_when_no_signals_at_all(make_ctx):
    ctx = make_ctx()
    kept, rejected = process_signals([Company(name="Quiet Co")], ctx)
    assert kept == [] and rejected[0][1] == "no buying signal found"


def test_reason_when_only_wrong_types(make_ctx):
    ctx = make_ctx(signals={"types": ["job_posting"]})
    c = Company(name="Acme", signals=[Signal(type="funding", title="Series B", posted_at=days_ago(1))])
    _, rejected = process_signals([c], ctx)
    assert rejected[0][1] == "no signal of an accepted type [job_posting] (1 signal found: 1 of another type (funding))"


def test_reason_lists_long_keyword_lists_compactly(make_ctx):
    kws = [f"role{i}" for i in range(8)]
    ctx = make_ctx(signals={"match_keywords": kws})
    _, rejected = process_signals([Company(name="A", signals=[job("Other", 1)])], ctx)
    assert "role4, +3 more]" in rejected[0][1]


def test_require_false_keeps_company_without_signals(make_ctx):
    ctx = make_ctx(signals={"require": False, "match_keywords": ["accountant"]})
    c = Company(name="Acme", signals=[job("Sales Rep", 1)])
    kept, rejected = process_signals([c], ctx)
    assert kept == [c] and rejected == [] and c.signals == []


def test_exact_duplicates_are_collapsed_but_distinct_postings_kept(make_ctx):
    ctx = make_ctx()
    a = job("Accountant", 3, external_id="1", url="https://jobs/1")
    dup = job("Accountant", 3, external_id="1", url="https://jobs/1")
    other = job("Accountant", 3, external_id="2", url="https://jobs/2")
    c = Company(name="Acme", signals=[a, dup, other])
    process_signals([c], ctx)
    assert len(c.signals) == 2


def test_non_signal_entries_are_ignored(make_ctx):
    ctx = make_ctx()
    c = Company(name="Acme", signals=[job("Accountant", 1), {"title": "not a signal"}])  # type: ignore[list-item]
    kept, _ = process_signals([c], ctx)
    assert kept and len(c.signals) == 1


def test_multiple_companies_split(make_ctx):
    ctx = make_ctx(signals={"match_keywords": ["accountant"]})
    good = Company(name="Good", domain="good.com", signals=[job("Accountant", 1)])
    bad = Company(name="Bad", domain="bad.com", signals=[job("Chef", 1)])
    kept, rejected = process_signals([good, bad], ctx)
    assert kept == [good] and [c for c, _ in rejected] == [bad]


# --- signal_stats -------------------------------------------------------------------------

def test_signal_stats_full(make_ctx):
    ctx = make_ctx(signals={"stale_after_days": 21})
    c = Company(name="Acme", signals=[
        job("Accountant", 2),
        job("Controller - immediate start", 9),
        job("AP Clerk", 25),
        Signal(type="funding", title="Series A", posted_at=days_ago(5)),
        Signal(type="leadership_change", title="New CFO"),
    ])
    st = signal_stats(c, ctx)
    assert [s.title for s in st["primary"]] == ["Accountant", "Controller - immediate start", "AP Clerk"]
    assert [s.title for s in st["secondary"]] == ["Series A", "New CFO"]
    assert st["freshest_age"] == 2 and st["volume"] == 3
    assert st["persistent"] is True     # AP Clerk is 25 days old >= 21
    assert st["urgent"] is True         # "immediate"
    assert st["secondary_types"] == {"funding", "leadership_change"}


def test_signal_stats_quiet_company(make_ctx):
    ctx = make_ctx()
    c = Company(name="Acme", signals=[job("Accountant", 3, description="A calm, steady role.")])
    st = signal_stats(c, ctx)
    assert st["persistent"] is False and st["urgent"] is False and st["volume"] == 1
    assert st["secondary_types"] == set()


def test_signal_stats_urgency_in_description_and_undated(make_ctx):
    ctx = make_ctx()
    c = Company(name="Acme", signals=[job("Accountant", description="We need someone ASAP.")])
    st = signal_stats(c, ctx)
    assert st["urgent"] is True and st["freshest_age"] is None and st["persistent"] is False


def test_signal_stats_urgency_ignores_secondary(make_ctx):
    ctx = make_ctx()
    c = Company(name="Acme", signals=[Signal(type="funding", title="Urgent: we raised")])
    st = signal_stats(c, ctx)
    assert st["urgent"] is False and st["primary"] == [] and st["freshest_age"] is None


def test_signal_stats_empty_primary_config(make_ctx):
    ctx = make_ctx(signals={"primary": []})
    c = Company(name="Acme", signals=[job("Accountant", 1)])
    st = signal_stats(c, ctx)
    assert st["volume"] == 0 and st["secondary_types"] == {"job_posting"}


@pytest.mark.parametrize("age,stale,expected", [(20, 21, False), (21, 21, True), (5, None, False)])
def test_signal_stats_stale_threshold(make_ctx, age, stale, expected):
    ctx = make_ctx(signals={"stale_after_days": stale})
    c = Company(name="Acme", signals=[job("Accountant", age)])
    assert signal_stats(c, ctx)["persistent"] is expected


# --- regressions (verifier findings) -----------------------------------------------------

def test_match_description_false_requires_title_hit(make_ctx):
    ctx = make_ctx(signals={"match_keywords": ["accountant"], "match_description": False})
    c = Company(name="Acme", signals=[
        job("Sales Manager", 1, description="You will work closely with our accountant"),
        job("Staff Accountant", 1),
    ])
    process_signals([c], ctx)
    assert [s.title for s in c.signals] == ["Staff Accountant"]
    # default (true) keeps the description fallback
    ctx2 = make_ctx(signals={"match_keywords": ["accountant"]})
    c2 = Company(name="Acme", signals=[job("Sales Manager", 1, description="work with our accountant")])
    kept, _ = process_signals([c2], ctx2)
    assert kept == [c2]


def test_match_description_false_rejects_company_with_description_only_hit(make_ctx):
    ctx = make_ctx(signals={"match_keywords": ["accountant"], "match_description": "false"})
    c = Company(name="Acme", signals=[job("Sales Manager", 1, description="our accountant")])
    kept, rejected = process_signals([c], ctx)
    assert kept == [] and len(rejected) == 1


@pytest.mark.parametrize("text,kw", [
    ("We build new homes in Texas", "news"),
    ("Competitive rates", "rat"),
    ("Our team really cares", "car"),
    ("Release notes", "not"),
    ("Good people", "goods"),
])
def test_plural_rule_does_not_make_other_words(text, kw):
    assert keyword_match(text, [kw]) is None


def test_plural_rule_still_handles_real_plurals():
    assert tokens_equal("tax", "taxes") and tokens_equal("church", "churches")
    assert tokens_equal("wish", "wishes") and tokens_equal("hero", "heroes")
    assert tokens_equal("business", "businesses") and tokens_equal("rate", "rates")
    assert not tokens_equal("rat", "rates") and not tokens_equal("new", "news")
    assert keyword_match("Latest news", ["news"]) == "news"


def test_acronym_keywords_only_match_capitals():
    assert keyword_match("Account Executive - make it grow", ["IT"]) is None
    assert keyword_match("Get in touch with us today", ["US"]) is None
    assert keyword_match("make it grow", ["it"]) is None
    assert keyword_match("Head of IT", ["IT"]) == "IT"
    assert keyword_match("IT Support Engineer", ["it"]) == "it"
    assert keyword_match("Offices across the US", ["US"]) == "US"
    assert keyword_match("Head of IT, EMEA", ["Head of IT"]) == "Head of IT"
    assert keyword_match("we are the head of it all", ["Head of IT"]) is None
    # an all-caps phrase keyword does not make its small words case-sensitive
    assert keyword_match("Head of Finance", ["HEAD OF FINANCE"]) == "HEAD OF FINANCE"
    # other short keywords stay case-insensitive
    assert keyword_match("hr business partner", ["HR"]) == "HR"


def test_acronym_keyword_in_signal_match_keywords(make_ctx):
    ctx = make_ctx(signals={"match_keywords": ["developer", "IT"]})
    c = Company(name="Acme", signals=[
        job("Account Executive", 1, description="Help us make it grow"),
        job("IT Manager", 2),
    ])
    process_signals([c], ctx)
    assert [s.title for s in c.signals] == ["IT Manager"]


def test_same_role_in_two_locations_is_not_a_repost_on_first_sighting(make_ctx):
    ctx = make_ctx()
    c = Company(name="Acme", domain="acme.com", signals=[
        job("Staff Accountant", 4, external_id="li-111", location="New York, NY"),
        job("Staff Accountant", 1, external_id="li-222", location="Boston, MA"),
    ])
    kept, _ = process_signals([c], ctx)
    assert [s.reposted for s in kept[0].signals] == [False, False]
    assert signal_stats(kept[0], ctx)["persistent"] is False
    # still both live on the next run: concurrent openings, not a re-post
    again = Company(name="Acme", domain="acme.com", signals=[
        job("Staff Accountant", 5, external_id="li-111", location="New York, NY"),
        job("Staff Accountant", 2, external_id="li-222", location="Boston, MA"),
    ])
    process_signals([again], ctx)
    assert not any(s.reposted for s in again.signals)


def test_same_ad_from_two_sources_is_not_a_repost(make_ctx):
    ctx = make_ctx()
    c = Company(name="Acme", domain="acme.com", signals=[
        job("Staff Accountant", 1, external_id="adzuna-1", url="https://acme.com/jobs/1"),
        job("Staff Accountant", 1, external_id="gh-99", url="https://acme.com/jobs/1"),
    ])
    process_signals([c], ctx)
    assert not any(s.reposted for s in c.signals)


def test_source_supplied_repost_flag_is_kept_with_siblings(make_ctx):
    ctx = make_ctx()
    c = Company(name="Acme", domain="acme.com", signals=[
        job("Staff Accountant", 1, external_id="a1", reposted=True),
        job("Staff Accountant", 2, external_id="a2"),
    ])
    process_signals([c], ctx)
    assert [(s.external_id, s.reposted) for s in c.signals] == [("a1", True), ("a2", False)]
