"""Tests for ``leadgen.doctor`` - one free check call per credential, never a paid lookup."""
from __future__ import annotations

import json
import types

import pytest
import requests

from leadgen import doctor as doctor_mod
from leadgen import registry
from leadgen.doctor import (CHECKS, FAILED, MISSING_KEY, OK, SKIPPED, Doctor, DoctorResult, format_results,
                            run_doctor)
from leadgen.http import HttpClient, HttpError
from leadgen.sources.base import Source

# --- canned payloads (documented response shapes) --------------------------------------------------

APOLLO_HEALTH = "https://api.apollo.io/v1/auth/health"
HUNTER_ACCOUNT = "https://api.hunter.io/v2/account"
MV_CREDITS = "https://api.millionverifier.com/api/v3/credits"
ZB_CREDITS = "https://api.zerobounce.net/v2/getcredits"
NB_INFO = "https://api.neverbounce.com/v4/account/info"
THEIRSTACK_BALANCE = "https://api.theirstack.com/v0/billing/credit-balance"
ADZUNA_SEARCH = "https://api.adzuna.com/v1/api/jobs/gb/search/1"
APIFY_ME = "https://api.apify.com/v2/users/me"
ANTHROPIC_MODEL = "https://api.anthropic.com/v1/models/claude-sonnet-5"
OPENAI_MODELS = "https://api.openai.com/v1/models"
INSTANTLY_CAMPAIGNS = "https://api.instantly.ai/api/v2/campaigns"
SMARTLEAD_CAMPAIGNS = "https://server.smartlead.ai/api/v1/campaigns"

FREE_ENDPOINTS = (APOLLO_HEALTH, HUNTER_ACCOUNT, MV_CREDITS, ZB_CREDITS, NB_INFO, THEIRSTACK_BALANCE,
                  "https://api.adzuna.com/v1/api/jobs/", APIFY_ME, "https://api.anthropic.com/v1/models",
                  OPENAI_MODELS, INSTANTLY_CAMPAIGNS, SMARTLEAD_CAMPAIGNS)


def hunter_account(searches=(30, 500), verifications=(12, 1000), credits=None):
    requests_ = {"searches": {"used": searches[0], "available": searches[1]},
                 "verifications": {"used": verifications[0], "available": verifications[1]}}
    if credits:
        requests_["credits"] = {"used": credits[0], "available": credits[1]}
    return {"data": {"first_name": "Sam", "last_name": "Lee", "email": "sam@agency.example",
                     "plan_name": "Starter", "plan_level": 1, "reset_date": "2026-10-01", "team_id": 12345,
                     "calls": {"_deprecation_notice": "Sums the searches and the verifications",
                               "used": 42, "available": 1500},
                     "requests": requests_}}


NB_ACCOUNT = {"status": "success", "billing_type": "default",
              "credits_info": {"paid_credits_used": 0, "free_credits_used": 0,
                               "paid_credits_remaining": 1000, "free_credits_remaining": 250},
              "job_counts": {"completed": 0, "under_review": 0, "queued": 0, "processing": 0},
              "execution_time": 150}

ADZUNA_RESULTS = {"__CLASS__": "Adzuna::API::Response::JobSearchResults", "count": 12873, "mean": 42000.5,
                  "results": [{"id": "4412345678", "title": "Senior Accountant",
                               "company": {"display_name": "Acme Ltd"}, "created": "2026-09-22T08:00:00Z",
                               "location": {"display_name": "Leeds, West Yorkshire"}}]}

APIFY_USER = {"data": {"id": "HGzIk8z78YcAPEB", "username": "sam-lee", "email": "sam@agency.example",
                       "plan": {"id": "FREE", "description": "Free plan", "isEnabled": True,
                                "monthlyBasePriceUsd": 0, "monthlyUsageCreditsUsd": 5}}}

ANTHROPIC_MODEL_INFO = {"type": "model", "id": "claude-sonnet-5", "display_name": "Claude Sonnet 5",
                        "created_at": "2026-02-01T00:00:00Z"}

OPENAI_MODEL_LIST = {"object": "list", "data": [
    {"id": "gpt-5-mini", "object": "model", "created": 1754000000, "owned_by": "system"},
    {"id": "gpt-5", "object": "model", "created": 1754000000, "owned_by": "system"}]}

SMARTLEAD_LIST = [{"id": 372, "user_id": 124, "created_at": "2026-08-01T10:00:00Z", "name": "Finance leaders",
                   "status": "ACTIVE"},
                  {"id": 373, "user_id": 124, "created_at": "2026-08-05T10:00:00Z", "name": "HR leaders",
                   "status": "PAUSED"}]


def ctx_for(make_ctx, env=None, mode="delivery", **pb):
    return make_ctx(env=env or {}, mode=mode, **pb)


def one(ctx, kind, cfg):
    """Check a single playbook entry."""
    return Doctor(ctx).entry(kind, cfg)


def find(results, kind, type_):
    return [r for r in results if r.kind == kind and r.type == type_]


def assert_no_paid_lookups(ctx):
    assert ctx.usage.paid_lookups == 0
    assert ctx.usage.rows() == []  # the doctor bypasses the meter entirely


# --- defaults / structure ------------------------------------------------------------------------

def test_default_playbook_needs_no_keys(make_ctx):
    ctx = ctx_for(make_ctx)
    results = run_doctor(ctx)
    assert [(r.kind, r.type) for r in results] == [("verifier", "basic"), ("exporter", "csv"),
                                                   ("notifier", "console")]
    assert all(r.status == SKIPPED and r.detail == "no key needed" for r in results)
    assert ctx.http.calls == []


def test_ordered_results_for_a_full_playbook_and_no_paid_lookups(make_ctx, tmp_path):
    data = tmp_path / "companies.csv"
    data.write_text("name,domain\nAcme,acme.com\n", encoding="utf-8")
    env = {"APOLLO_API_KEY": "ap-key-1", "HUNTER_API_KEY": "hu-key-1", "MILLIONVERIFIER_API_KEY": "mv-key-1",
           "THEIRSTACK_API_KEY": "ts-key-1", "ANTHROPIC_API_KEY": "sk-ant-1"}
    ctx = ctx_for(make_ctx, env=env,
                  usage={"max_paid_lookups": 1},  # a budget must not block the doctor
                  sources=[{"type": "theirstack"}, {"type": "csv", "path": str(data)},
                           {"type": "greenhouse", "companies": [{"name": "Acme", "board": "acme"}]},
                           {"type": "apollo", "enabled": False}],
                  enrichment={"finders": [{"type": "apollo"}, {"type": "hunter"}, {"type": "pattern"}],
                              "verifier": {"type": "millionverifier"}},
                  writer={"provider": "anthropic", "model": "claude-sonnet-5"},
                  outbound={"exporters": [{"type": "csv"}, {"type": "json", "enabled": False}]},
                  notify={"channels": [{"type": "console"}, {"type": "slack"}]})
    ctx.http.add("GET", THEIRSTACK_BALANCE, json={"ui_credits": 50, "api_credits": 200, "used_api_credits": 12})
    ctx.http.add("GET", APOLLO_HEALTH, json={"healthy": True, "is_logged_in": True})
    ctx.http.add("GET", HUNTER_ACCOUNT, json=hunter_account())
    ctx.http.add("GET", MV_CREDITS, json={"credits": 9950})
    ctx.http.add("GET", ANTHROPIC_MODEL, json=ANTHROPIC_MODEL_INFO)

    results = run_doctor(ctx)
    assert [(r.kind, r.type, r.status) for r in results] == [
        ("source", "theirstack", OK), ("source", "csv", SKIPPED), ("source", "greenhouse", SKIPPED),
        ("finder", "apollo", OK), ("finder", "hunter", OK), ("finder", "pattern", SKIPPED),
        ("verifier", "millionverifier", OK), ("llm", "anthropic", OK),
        ("exporter", "csv", SKIPPED), ("notifier", "console", SKIPPED), ("notifier", "slack", MISSING_KEY),
    ]
    assert len(ctx.http.calls) == 5  # one call per credential
    assert all(c["url"].startswith(FREE_ENDPOINTS) for c in ctx.http.calls)
    assert_no_paid_lookups(ctx)
    llm = find(results, "llm", "anthropic")[0]
    assert llm.label == "writer (claude-sonnet-5)"
    assert find(results, "verifier", "millionverifier")[0].quota == "9,950 credits left"
    assert "SLACK_WEBHOOK_URL" in find(results, "notifier", "slack")[0].detail


def test_writer_llm_only_when_provider_set(make_ctx):
    ctx = ctx_for(make_ctx, writer={"type": "template", "provider": ""})
    assert find(run_doctor(ctx), "llm", "anthropic") == []
    ctx2 = ctx_for(make_ctx, env={}, writer={"type": "template", "provider": "anthropic"})
    res = find(run_doctor(ctx2), "llm", "anthropic")
    assert len(res) == 1 and res[0].status == MISSING_KEY and "ANTHROPIC_API_KEY" in res[0].detail


def test_label_from_config(make_ctx):
    ctx = ctx_for(make_ctx, env={"APOLLO_API_KEY": "k-apollo"})
    ctx.http.add("GET", APOLLO_HEALTH, json={"is_logged_in": True})
    res = one(ctx, "source", {"type": "apollo", "label": "Apollo US"})
    assert (res.kind, res.type, res.label, res.status) == ("source", "apollo", "Apollo US", OK)


# --- Apollo ------------------------------------------------------------------------------------------

def test_apollo_ok_request_shape(make_ctx):
    ctx = ctx_for(make_ctx, env={"APOLLO_API_KEY": "ap-SECRET-1"})
    ctx.http.add("GET", APOLLO_HEALTH, json={"healthy": True, "is_logged_in": True})
    res = one(ctx, "source", {"type": "apollo"})
    assert res.status == OK and "key accepted" in res.detail
    call = ctx.http.calls[0]
    assert call["method"] == "GET" and call["url"] == APOLLO_HEALTH
    assert call["headers"]["x-api-key"] == "ap-SECRET-1" and call["params"] == {}
    assert call["timeout"] == doctor_mod.TIMEOUT
    assert_no_paid_lookups(ctx)


def test_apollo_not_logged_in_is_rejected(make_ctx):
    ctx = ctx_for(make_ctx, env={"APOLLO_API_KEY": "k-apollo"})
    ctx.http.add("GET", APOLLO_HEALTH, json={"healthy": True, "is_logged_in": False})
    res = one(ctx, "finder", {"type": "apollo"})
    assert res.status == FAILED and "key rejected" in res.detail and "$APOLLO_API_KEY" in res.detail


def test_apollo_401_is_rejected_without_leaking_the_key(make_ctx):
    ctx = ctx_for(make_ctx, env={"APOLLO_API_KEY": "ap-SECRET-1"})
    ctx.http.add("GET", APOLLO_HEALTH, status=401,
                 json={"error": "Invalid access credentials for key ap-SECRET-1."})
    res = one(ctx, "source", {"type": "apollo"})
    assert res.status == FAILED and res.detail.startswith("key rejected (HTTP 401: Invalid access credentials")
    assert "ap-SECRET-1" not in res.detail and "***" in res.detail


def test_apollo_unexpected_body(make_ctx):
    ctx = ctx_for(make_ctx, env={"APOLLO_API_KEY": "k-apollo"})
    ctx.http.add("GET", APOLLO_HEALTH, json={"healthy": True})
    assert "unexpected answer" in one(ctx, "source", {"type": "apollo"}).detail


def test_missing_key_names_the_variable_and_makes_no_call(make_ctx):
    ctx = ctx_for(make_ctx, env={})
    res = one(ctx, "source", {"type": "apollo"})
    assert res.status == MISSING_KEY
    assert res.detail == "missing key - set $APOLLO_API_KEY (or 'api_key' in the playbook)"
    res2 = one(ctx, "source", {"type": "apollo", "api_key_env": "APOLLO_KEY_CLIENT_A"})
    assert "$APOLLO_KEY_CLIENT_A" in res2.detail
    assert ctx.http.calls == []


def test_api_key_env_and_base_url_are_honoured(make_ctx):
    ctx = ctx_for(make_ctx, env={"APOLLO_KEY_CLIENT_A": "k-client-a"})
    ctx.http.add("GET", "https://apollo-proxy.example/v1/auth/health", json={"is_logged_in": True})
    res = one(ctx, "source", {"type": "apollo", "api_key_env": "APOLLO_KEY_CLIENT_A",
                              "base_url": "https://apollo-proxy.example/"})
    assert res.status == OK and ctx.http.calls[0]["headers"]["x-api-key"] == "k-client-a"


def test_same_key_is_tested_once(make_ctx):
    ctx = ctx_for(make_ctx, env={"APOLLO_API_KEY": "k-apollo"}, sources=[{"type": "apollo"}],
                  enrichment={"finders": [{"type": "apollo"}], "verifier": None})
    ctx.http.add("GET", APOLLO_HEALTH, json={"is_logged_in": True})
    results = run_doctor(ctx)
    src, fnd = find(results, "source", "apollo")[0], find(results, "finder", "apollo")[0]
    assert src.status == fnd.status == OK
    assert len(ctx.http.calls) == 1
    assert "tested once" in fnd.detail and "tested once" not in src.detail


def test_different_keys_are_tested_separately(make_ctx):
    ctx = ctx_for(make_ctx, env={"APOLLO_API_KEY": "k-one"}, sources=[{"type": "apollo"}],
                  enrichment={"finders": [{"type": "apollo", "api_key": "k-two"}], "verifier": None})
    ctx.http.add("GET", APOLLO_HEALTH, fn=lambda call: {"is_logged_in": call["headers"]["x-api-key"] == "k-one"})
    results = run_doctor(ctx)
    assert len(ctx.http.calls) == 2
    assert find(results, "source", "apollo")[0].status == OK
    bad = find(results, "finder", "apollo")[0]
    assert bad.status == FAILED and "'api_key' in the playbook" in bad.detail and "k-two" not in bad.detail


# --- Hunter ------------------------------------------------------------------------------------------

def test_hunter_quota_text_and_request_shape(make_ctx):
    ctx = ctx_for(make_ctx, env={"HUNTER_API_KEY": "hu-SECRET"})
    ctx.http.add("GET", HUNTER_ACCOUNT, json=hunter_account())
    res = one(ctx, "finder", {"type": "hunter"})
    assert res.status == OK and res.detail == "key accepted"
    assert res.quota == ("searches: 30 of 500 used; verifications: 12 of 1,000 used "
                         "(plan Starter, resets 2026-10-01)")
    call = ctx.http.calls[0]
    assert call["url"] == HUNTER_ACCOUNT and call["params"] == {"api_key": "hu-SECRET"}


def test_hunter_finder_and_verifier_share_one_call(make_ctx):
    ctx = ctx_for(make_ctx, env={"HUNTER_API_KEY": "hu-key"},
                  enrichment={"finders": [{"type": "hunter"}], "verifier": {"type": "hunter"}})
    ctx.http.add("GET", HUNTER_ACCOUNT, json=hunter_account(credits=(10, 100)))
    results = run_doctor(ctx)
    assert len(ctx.http.calls_to("hunter.io")) == 1
    ver = find(results, "verifier", "hunter")[0]
    assert ver.status == OK and "credits: 10 of 100 used" in ver.quota and "tested once" in ver.detail


def test_hunter_exhausted_quota_warns_for_the_relevant_counter(make_ctx):
    ctx = ctx_for(make_ctx, env={"HUNTER_API_KEY": "hu-key"})
    ctx.http.add("GET", HUNTER_ACCOUNT, json=hunter_account(searches=(500, 500), verifications=(3, 1000)))
    finder = one(ctx, "finder", {"type": "hunter"})
    assert finder.status == OK and "WARNING: no searches left" in finder.detail
    ctx2 = ctx_for(make_ctx, env={"HUNTER_API_KEY": "hu-key"})
    ctx2.http.add("GET", HUNTER_ACCOUNT, json=hunter_account(searches=(500, 500), verifications=(3, 1000)))
    assert "WARNING" not in one(ctx2, "verifier", {"type": "hunter"}).detail


def test_hunter_rejected_and_malformed(make_ctx):
    ctx = ctx_for(make_ctx, env={"HUNTER_API_KEY": "hu-key"})
    ctx.http.add("GET", HUNTER_ACCOUNT, status=401, json={"errors": [
        {"id": "authentication_failed", "code": 401, "details": "No user found for the API key supplied"}]})
    res = one(ctx, "finder", {"type": "hunter"})
    assert res.status == FAILED and "key rejected (HTTP 401: No user found" in res.detail
    ctx2 = ctx_for(make_ctx, env={"HUNTER_API_KEY": "hu-key"})
    ctx2.http.add("GET", HUNTER_ACCOUNT, json={"meta": {}})
    assert one(ctx2, "finder", {"type": "hunter"}).detail.startswith("unexpected answer from Hunter")


# --- email verifiers ---------------------------------------------------------------------------------

def test_millionverifier_credits(make_ctx):
    ctx = ctx_for(make_ctx, env={"MILLIONVERIFIER_API_KEY": "mv-SECRET"})
    ctx.http.add("GET", MV_CREDITS, json={"credits": 9950})
    res = one(ctx, "verifier", {"type": "millionverifier"})
    assert (res.status, res.detail, res.quota) == (OK, "key accepted", "9,950 credits left")
    assert ctx.http.calls[0]["params"] == {"api": "mv-SECRET"}
    assert_no_paid_lookups(ctx)


@pytest.mark.parametrize("body, status, needle", [
    ({"error": "Invalid API key", "credits": 0}, FAILED, "key rejected - MillionVerifier says: Invalid API key"),
    ({"error": "Service temporarily unavailable"}, FAILED, "MillionVerifier says: Service temporarily"),
    ({"credits": 0}, OK, "WARNING: no credits left"),
    ({"credits": "12.5"}, OK, "key accepted"),
    ({"something": 1}, FAILED, "no 'credits' field"),
    (["x"], FAILED, "not an object"),
])
def test_millionverifier_answers(make_ctx, body, status, needle):
    ctx = ctx_for(make_ctx, env={"MILLIONVERIFIER_API_KEY": "mv-key"})
    ctx.http.add("GET", MV_CREDITS, json=body)
    res = one(ctx, "verifier", {"type": "millionverifier"})
    assert res.status == status and needle in res.detail


def test_zerobounce_credits_and_invalid_key(make_ctx):
    ctx = ctx_for(make_ctx, env={"ZEROBOUNCE_API_KEY": "zb-SECRET"})
    ctx.http.add("GET", ZB_CREDITS, json={"Credits": "2375"})
    res = one(ctx, "verifier", {"type": "zerobounce"})
    assert (res.status, res.quota) == (OK, "2,375 credits left")
    assert ctx.http.calls[0]["params"] == {"api_key": "zb-SECRET"}

    ctx2 = ctx_for(make_ctx, env={"ZEROBOUNCE_API_KEY": "zb-bad"})
    ctx2.http.add("GET", ZB_CREDITS, json={"Credits": "-1"})
    res2 = one(ctx2, "verifier", {"type": "zerobounce"})
    assert res2.status == FAILED and res2.detail.startswith("key rejected") and "-1" in res2.detail


def test_zerobounce_regional_base_url(make_ctx):
    ctx = ctx_for(make_ctx, env={"ZEROBOUNCE_API_KEY": "zb-key"})
    ctx.http.add("GET", "https://api-eu.zerobounce.net/v2/getcredits", json={"Credits": "10"})
    res = one(ctx, "verifier", {"type": "zerobounce", "base_url": "https://api-eu.zerobounce.net/v2"})
    assert res.status == OK


def test_neverbounce_credits_and_auth_failure_echoing_the_key(make_ctx):
    ctx = ctx_for(make_ctx, env={"NEVERBOUNCE_API_KEY": "nb-SECRET"})
    ctx.http.add("GET", NB_INFO, json=NB_ACCOUNT)
    res = one(ctx, "verifier", {"type": "neverbounce"})
    assert (res.status, res.quota) == (OK, "1,000 paid credits + 250 free credits left")
    assert ctx.http.calls[0]["params"] == {"key": "nb-SECRET"}

    ctx2 = ctx_for(make_ctx, env={"NEVERBOUNCE_API_KEY": "nb-SECRET"})
    ctx2.http.add("GET", NB_INFO, json={"status": "auth_failure", "message": "Invalid API key 'nb-SECRET'",
                                        "execution_time": 1})
    res2 = one(ctx2, "verifier", {"type": "neverbounce"})
    assert res2.status == FAILED and res2.detail.startswith("key rejected")
    assert "nb-SECRET" not in res2.detail and "Invalid API key '***'" in res2.detail


def test_neverbounce_other_failure_and_zero_credits(make_ctx):
    ctx = ctx_for(make_ctx, env={"NEVERBOUNCE_API_KEY": "nb-key"})
    ctx.http.add("GET", NB_INFO, json={"status": "temp_unavail", "message": "try later"})
    res = one(ctx, "verifier", {"type": "neverbounce"})
    assert res.status == FAILED and "temp_unavail" in res.detail and "try later" in res.detail
    ctx2 = ctx_for(make_ctx, env={"NEVERBOUNCE_API_KEY": "nb-key"})
    ctx2.http.add("GET", NB_INFO, json=dict(NB_ACCOUNT, credits_info={"paid_credits_remaining": 0,
                                                                      "free_credits_remaining": 0}))
    assert "WARNING: no credits left" in one(ctx2, "verifier", {"type": "neverbounce"}).detail


# --- job sources ---------------------------------------------------------------------------------------

def test_theirstack_balance_bearer_and_host_from_base_url(make_ctx):
    ctx = ctx_for(make_ctx, env={"THEIRSTACK_API_KEY": "ts-SECRET"})
    ctx.http.add("GET", THEIRSTACK_BALANCE, json={"ui_credits": 50, "api_credits": 200, "used_api_credits": 12})
    res = one(ctx, "source", {"type": "theirstack"})
    assert res.status == OK and res.quota == "API credits 200 (12 used), web-app credits 50"
    call = ctx.http.calls[0]
    assert call["url"] == THEIRSTACK_BALANCE and call["headers"]["Authorization"] == "Bearer ts-SECRET"

    ctx2 = ctx_for(make_ctx, env={"THEIRSTACK_API_KEY": "ts-key"})
    ctx2.http.add("GET", "https://ts-proxy.example/v0/billing/credit-balance", json={"api_credits": 5})
    res2 = one(ctx2, "source", {"type": "theirstack", "base_url": "https://ts-proxy.example/v1"})
    assert res2.status == OK and res2.quota == "API credits 5"


def test_doctor_url_override(make_ctx):
    ctx = ctx_for(make_ctx, env={"THEIRSTACK_API_KEY": "ts-key"})
    ctx.http.add("GET", "https://api.theirstack.com/v1/billing/balance", json={"api_credits": 7})
    res = one(ctx, "source", {"type": "theirstack",
                              "doctor_url": "https://api.theirstack.com/v1/billing/balance"})
    assert res.status == OK and ctx.http.calls[0]["url"] == "https://api.theirstack.com/v1/billing/balance"


def test_endpoint_moved_suggests_doctor_url(make_ctx):
    ctx = ctx_for(make_ctx, env={"THEIRSTACK_API_KEY": "ts-key"})
    ctx.http.add("GET", THEIRSTACK_BALANCE, status=404, json={"detail": "Not Found"})
    res = one(ctx, "source", {"type": "theirstack"})
    assert res.status == FAILED and "HTTP 404" in res.detail and "doctor_url" in res.detail
    assert "api.theirstack.com" in res.detail


def test_adzuna_uses_first_country_and_both_keys(make_ctx):
    ctx = ctx_for(make_ctx, env={"ADZUNA_APP_ID": "az-id-1", "ADZUNA_APP_KEY": "az-SECRET"})
    ctx.http.add("GET", "https://api.adzuna.com/v1/api/jobs/us/search/1", json=ADZUNA_RESULTS)
    res = one(ctx, "source", {"type": "adzuna", "countries": ["us", "ca"], "queries": ["accountant"]})
    assert res.status == OK and "US" in res.detail and "12,873 jobs" in res.detail
    params = ctx.http.calls[0]["params"]
    assert params["app_id"] == "az-id-1" and params["app_key"] == "az-SECRET" and params["results_per_page"] == 1
    assert "what" not in params  # a minimal search, not the configured queries


def test_adzuna_defaults_to_gb_and_reports_both_missing_keys(make_ctx):
    ctx = ctx_for(make_ctx, env={"ADZUNA_APP_ID": "id", "ADZUNA_APP_KEY": "key"})
    ctx.http.add("GET", ADZUNA_SEARCH, json=ADZUNA_RESULTS)
    assert one(ctx, "source", {"type": "adzuna"}).status == OK
    ctx2 = ctx_for(make_ctx, env={})
    res = one(ctx2, "source", {"type": "adzuna", "countries": ["gb"]})
    assert res.status == MISSING_KEY and "$ADZUNA_APP_ID and $ADZUNA_APP_KEY" in res.detail
    ctx3 = ctx_for(make_ctx, env={"ADZUNA_APP_ID": "id"})
    res3 = one(ctx3, "source", {"type": "adzuna", "countries": ["gb"]})
    assert "$ADZUNA_APP_KEY" in res3.detail and "APP_ID" not in res3.detail


def test_adzuna_rejected_keys_hint_both_variables(make_ctx):
    ctx = ctx_for(make_ctx, env={"ADZUNA_APP_ID": "id", "ADZUNA_APP_KEY": "az-SECRET"})
    ctx.http.add("GET", ADZUNA_SEARCH, status=401,
                 json={"exception": "AUTH_FAIL", "display": "Authorisation failed", "doc": "..."})
    res = one(ctx, "source", {"type": "adzuna", "countries": ["uk"]})
    assert res.status == FAILED and "Authorisation failed" in res.detail
    assert "$ADZUNA_APP_ID / $ADZUNA_APP_KEY" in res.detail


def test_apify_user_and_plan(make_ctx):
    ctx = ctx_for(make_ctx, env={"APIFY_TOKEN": "apify_api_SECRET"})
    ctx.http.add("GET", APIFY_ME, json=APIFY_USER)
    res = one(ctx, "source", {"type": "apify", "actor": "compass/crawler-google-places"})
    assert (res.status, res.detail) == (OK, "token accepted (user sam-lee)")
    assert res.quota == "plan FREE, $5 platform credits per month"
    assert ctx.http.calls[0]["params"] == {"token": "apify_api_SECRET"}
    assert "own risk" not in res.detail


def test_apify_scraper_presets_carry_the_risk_note(make_ctx):
    ctx = ctx_for(make_ctx, env={"APIFY_TOKEN": "tok-apify"})
    ctx.http.add("GET", APIFY_ME, json=APIFY_USER)
    li = one(ctx, "source", {"type": "linkedin_jobs", "actor": "someone/linkedin-jobs"})
    assert li.status == OK and "use at own risk" in li.detail and "LinkedIn" in li.detail
    indeed = one(ctx, "source", {"type": "apify", "preset": "indeed_jobs", "actor": "x/y"})
    assert "use at own risk" in indeed.detail and "Indeed" in indeed.detail
    ctx2 = ctx_for(make_ctx, env={})
    missing = one(ctx2, "source", {"type": "linkedin_jobs"})
    assert missing.status == MISSING_KEY and "$APIFY_TOKEN" in missing.detail and "own risk" in missing.detail


@pytest.mark.parametrize("type_", ["greenhouse", "lever", "ashby"])
def test_public_job_boards_need_no_key(make_ctx, type_):
    ctx = ctx_for(make_ctx)
    res = one(ctx, "source", {"type": type_, "companies": [{"name": "Acme", "board": "acme"}]})
    assert res.status == SKIPPED and "no key needed" in res.detail and ctx.http.calls == []


def test_file_sources_check_the_file(make_ctx, tmp_path):
    data = tmp_path / "leads.csv"
    data.write_text("name\nAcme\n", encoding="utf-8")
    ctx = ctx_for(make_ctx)
    ok = one(ctx, "source", {"type": "csv", "path": str(data)})
    assert ok.status == SKIPPED and "file found" in ok.detail
    missing = one(ctx, "finder", {"type": "csv", "path": str(tmp_path / "nope.csv")})
    assert missing.status == FAILED and "file not found" in missing.detail


# --- AI providers --------------------------------------------------------------------------------------

def test_anthropic_checks_key_and_model_in_one_free_call(make_ctx):
    ctx = ctx_for(make_ctx, env={"ANTHROPIC_API_KEY": "sk-ant-SECRET"},
                  writer={"provider": "anthropic", "model": "claude-sonnet-5"})
    ctx.http.add("GET", ANTHROPIC_MODEL, json=ANTHROPIC_MODEL_INFO)
    res = Doctor(ctx).writer_llm()
    assert res.status == OK and "model claude-sonnet-5 is available" in res.detail
    call = ctx.http.calls[0]
    assert call["url"] == ANTHROPIC_MODEL and call["method"] == "GET"
    assert call["headers"]["x-api-key"] == "sk-ant-SECRET" and call["headers"]["anthropic-version"] == "2023-06-01"
    assert_no_paid_lookups(ctx)


def test_anthropic_unknown_model_and_rejected_key(make_ctx):
    ctx = ctx_for(make_ctx, env={"ANTHROPIC_API_KEY": "sk-ant-key"},
                  writer={"provider": "anthropic", "model": "claude-nonexistent"})
    ctx.http.add("GET", "https://api.anthropic.com/v1/models/claude-nonexistent", status=404,
                 json={"type": "error", "error": {"type": "not_found_error", "message": "model: claude-nonexistent"}})
    res = Doctor(ctx).writer_llm()
    assert res.status == FAILED and "model 'claude-nonexistent' was not found" in res.detail
    assert "writer.model" in res.detail

    ctx2 = ctx_for(make_ctx, env={"ANTHROPIC_API_KEY": "sk-ant-SECRET"},
                   writer={"provider": "anthropic", "model": "claude-sonnet-5"})
    ctx2.http.add("GET", ANTHROPIC_MODEL, status=401, json={
        "type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}})
    res2 = Doctor(ctx2).writer_llm()
    assert res2.status == FAILED and "key rejected (HTTP 401: invalid x-api-key)" in res2.detail
    assert "$ANTHROPIC_API_KEY" in res2.detail and "sk-ant-SECRET" not in res2.detail


def test_anthropic_gateway_base_url_and_default_model(make_ctx):
    ctx = ctx_for(make_ctx, env={"ANTHROPIC_API_KEY": "k-anthropic"},
                  writer={"provider": "anthropic", "base_url": "https://gw.example.com/anthropic/v1/messages",
                          "llm": {"extra_headers": {"x-gateway": "on"}}})
    ctx.http.add("GET", "https://gw.example.com/anthropic/v1/models/claude-opus-5",
                 json={"type": "model", "id": "claude-opus-5", "display_name": "Claude Opus 5"})
    res = Doctor(ctx).writer_llm()
    assert res.status == OK and res.label == "writer (claude-opus-5)"
    assert ctx.http.calls[0]["headers"]["x-gateway"] == "on"


def test_anthropic_doctor_url_with_model_placeholder(make_ctx):
    ctx = ctx_for(make_ctx, env={"ANTHROPIC_API_KEY": "k-anthropic"},
                  writer={"provider": "anthropic", "model": "claude-sonnet-5",
                          "llm": {"doctor_url": "https://mirror.example/models/{model}"}})
    ctx.http.add("GET", "https://mirror.example/models/claude-sonnet-5", json=ANTHROPIC_MODEL_INFO)
    assert Doctor(ctx).writer_llm().status == OK


def test_openai_model_list(make_ctx):
    ctx = ctx_for(make_ctx, env={"OPENAI_API_KEY": "sk-SECRET"}, writer={"provider": "openai", "model": "gpt-5-mini"})
    ctx.http.add("GET", OPENAI_MODELS, json=OPENAI_MODEL_LIST)
    res = Doctor(ctx).writer_llm()
    assert (res.kind, res.type, res.status) == ("llm", "openai", OK)
    assert "model gpt-5-mini is available" in res.detail
    assert ctx.http.calls[0]["headers"]["Authorization"] == "Bearer sk-SECRET"


def test_openai_model_not_listed_is_a_warning(make_ctx):
    ctx = ctx_for(make_ctx, env={"OPENAI_API_KEY": "sk-key"}, writer={"provider": "openai", "model": "gpt-9"})
    ctx.http.add("GET", OPENAI_MODELS, json=OPENAI_MODEL_LIST)
    res = Doctor(ctx).writer_llm()
    assert res.status == OK and "WARNING: model 'gpt-9' is not in the list of 2 models" in res.detail


def test_openai_compatible_uses_its_own_key_and_base_url(make_ctx):
    ctx = ctx_for(make_ctx, env={"OPENROUTER_API_KEY": "or-SECRET", "OPENAI_API_KEY": "sk-never-sent"},
                  writer={"provider": "openai_compatible", "model": "meta/llama-4",
                          "base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY"})
    ctx.http.add("GET", "https://openrouter.ai/api/v1/models", json={"data": [{"id": "meta/llama-4"}]})
    res = Doctor(ctx).writer_llm()
    assert (res.type, res.status) == ("openai_compatible", OK)
    assert ctx.http.calls[0]["headers"]["Authorization"] == "Bearer or-SECRET"


def test_openai_third_party_host_never_gets_the_openai_key(make_ctx):
    ctx = ctx_for(make_ctx, env={"OPENAI_API_KEY": "sk-openai"},
                  writer={"provider": "openai", "model": "x", "base_url": "https://openrouter.ai/api/v1"})
    res = Doctor(ctx).writer_llm()
    assert res.status == MISSING_KEY and "api_key_env" in res.detail and ctx.http.calls == []


def test_openai_compatible_config_problem_and_keyless_server(make_ctx):
    ctx = ctx_for(make_ctx, env={}, writer={"provider": "openai_compatible"})
    res = Doctor(ctx).writer_llm()
    assert res.status == FAILED and res.detail.startswith("config problem") and "base_url" in res.detail

    ctx2 = ctx_for(make_ctx, env={}, writer={"provider": "openai_compatible", "model": "llama3",
                                             "base_url": "http://localhost:11434/v1",
                                             "llm": {"api_key_required": False}})
    ctx2.http.add("GET", "http://localhost:11434/v1/models", json={"object": "list", "data": [{"id": "llama3"}]})
    res2 = Doctor(ctx2).writer_llm()
    assert res2.status == OK and res2.detail.startswith("server answered (no key configured)")
    assert "Authorization" not in ctx2.http.calls[0]["headers"]


# --- outbound exporters / notifiers --------------------------------------------------------------------

def test_sending_tools_are_skipped_in_delivery_mode(make_ctx):
    ctx = ctx_for(make_ctx, env={"INSTANTLY_API_KEY": "k-inst"},
                  outbound={"exporters": [{"type": "instantly", "campaign_id": "c1"}, {"type": "instantly_csv"},
                                          {"type": "webhook"}, {"type": "csv"}]})
    results = run_doctor(ctx)
    for type_ in ("instantly", "instantly_csv", "webhook"):
        r = find(results, "exporter", type_)[0]
        assert r.status == SKIPPED and "not used in delivery mode" in r.detail
    assert ctx.http.calls == []


def test_instantly_in_outbound_mode(make_ctx):
    ctx = ctx_for(make_ctx, env={"INSTANTLY_API_KEY": "inst-SECRET"}, mode="outbound")
    ctx.http.add("GET", INSTANTLY_CAMPAIGNS, json={"items": [{"id": "c1", "name": "Q4"}], "next_starting_after": "c1"})
    res = one(ctx, "exporter", {"type": "instantly", "campaign_id": "c1"})
    assert res.status == OK
    call = ctx.http.calls[0]
    assert call["params"] == {"limit": 1} and call["headers"]["Authorization"] == "Bearer inst-SECRET"

    ctx2 = ctx_for(make_ctx, env={"INSTANTLY_API_KEY": "inst-key"}, mode="outbound")
    ctx2.http.add("GET", INSTANTLY_CAMPAIGNS, status=403, json={"statusCode": 403, "message": "Forbidden"})
    res2 = one(ctx2, "exporter", {"type": "instantly", "campaign_id": "c1"})
    assert res2.status == FAILED and "key rejected" in res2.detail and "campaigns:read" in res2.detail


def test_smartlead_campaigns(make_ctx):
    ctx = ctx_for(make_ctx, env={"SMARTLEAD_API_KEY": "sl-SECRET"}, mode="outbound")
    ctx.http.add("GET", SMARTLEAD_CAMPAIGNS, json=SMARTLEAD_LIST)
    res = one(ctx, "exporter", {"type": "smartlead", "campaign_id": 372})
    assert res.status == OK and res.detail == "key accepted (2 campaigns); campaign 372 found"
    assert ctx.http.calls[0]["params"] == {"api_key": "sl-SECRET"}
    res2 = one(ctx, "exporter", {"type": "smartlead", "campaign_id": "999"})
    assert res2.status == OK and "WARNING: campaign_id 999 is not one" in res2.detail


def test_posting_channels_are_never_called(make_ctx):
    ctx = ctx_for(make_ctx, env={"SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/T000/B000/XXXX"},
                  mode="outbound",
                  notify={"channels": [{"type": "slack"}, {"type": "webhook"}]},
                  outbound={"exporters": [{"type": "webhook", "url": "https://hook.example/abc"}]})
    results = run_doctor(ctx)
    slack = find(results, "notifier", "slack")[0]
    assert slack.status == SKIPPED and "would post a real message" in slack.detail
    hook = find(results, "notifier", "webhook")[0]
    assert hook.status == MISSING_KEY and "$LEADGEN_WEBHOOK_URL" in hook.detail
    exp = find(results, "exporter", "webhook")[0]
    assert exp.status == SKIPPED and "would post" in exp.detail
    assert ctx.http.calls == []
    assert "hooks.slack.com" not in " ".join(r.detail for r in results)


# --- Google Sheets ---------------------------------------------------------------------------------------

def _sa_file(tmp_path, **over):
    info = {"type": "service_account", "project_id": "leadgen-1", "private_key_id": "abc",
            "private_key": "-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----\n",
            "client_email": "leadgen@leadgen-1.iam.gserviceaccount.com", "client_id": "123"}
    info.update(over)
    path = tmp_path / "sa.json"
    path.write_text(json.dumps(info), encoding="utf-8")
    return path


@pytest.fixture
def fake_gspread(monkeypatch):
    mod = types.ModuleType("gspread")
    monkeypatch.setitem(__import__("sys").modules, "gspread", mod)
    return mod


def test_gsheets_ok_without_network(make_ctx, tmp_path, fake_gspread):
    path = _sa_file(tmp_path)
    ctx = ctx_for(make_ctx, env={"GOOGLE_APPLICATION_CREDENTIALS": str(path)})
    res = one(ctx, "exporter", {"type": "gsheets", "spreadsheet_id": "1AbC"})
    assert res.status == OK and "leadgen@leadgen-1.iam.gserviceaccount.com" in res.detail
    assert "PRIVATE KEY" not in res.detail and ctx.http.calls == []


@pytest.mark.parametrize("content, needle", [
    ("{not json", "not valid JSON"),
    (json.dumps(["x"]), "must be a JSON object"),
    (json.dumps({"type": "service_account"}), "no 'client_email'"),
    (json.dumps({"installed": {"client_id": "x"}, "type": "authorized_user", "client_email": "a@b.c"}),
     "'authorized_user' file, not a service-account key"),
])
def test_gsheets_bad_key_files(make_ctx, tmp_path, fake_gspread, content, needle):
    path = tmp_path / "sa.json"
    path.write_text(content, encoding="utf-8")
    ctx = ctx_for(make_ctx)
    res = one(ctx, "exporter", {"type": "gsheets", "spreadsheet_id": "1AbC", "service_account_file": str(path)})
    assert res.status == FAILED and needle in res.detail


def test_gsheets_missing_credentials_and_missing_file(make_ctx, tmp_path, fake_gspread):
    ctx = ctx_for(make_ctx)
    res = one(ctx, "exporter", {"type": "gsheets", "spreadsheet_id": "1AbC"})
    assert res.status == MISSING_KEY and "$GOOGLE_APPLICATION_CREDENTIALS" in res.detail
    res2 = one(ctx, "exporter", {"type": "gsheets", "spreadsheet_id": "1AbC",
                                 "service_account_file": str(tmp_path / "missing.json")})
    assert res2.status == FAILED and "not found" in res2.detail and "missing.json" in res2.detail


def test_gsheets_json_env_and_missing_sheet_id(make_ctx, fake_gspread):
    info = {"type": "service_account", "client_email": "bot@proj.iam.gserviceaccount.com", "private_key": "x"}
    ctx = ctx_for(make_ctx, env={"GOOGLE_SERVICE_ACCOUNT_JSON": json.dumps(info)})
    res = one(ctx, "exporter", {"type": "gsheets"})
    assert res.status == OK and "bot@proj" in res.detail and "WARNING: no spreadsheet_id" in res.detail
    ctx2 = ctx_for(make_ctx, env={"GOOGLE_SERVICE_ACCOUNT_JSON": "{broken"})
    assert "is not valid JSON" in one(ctx2, "exporter", {"type": "gsheets", "spreadsheet_id": "x"}).detail


def test_gsheets_without_gspread_installed(make_ctx, tmp_path, monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "gspread", None)  # import blocked
    path = _sa_file(tmp_path)
    ctx = ctx_for(make_ctx)
    res = one(ctx, "exporter", {"type": "gsheets", "spreadsheet_id": "1", "service_account_file": str(path)})
    assert res.status == FAILED and "pip install gspread" in res.detail


def test_client_google_sheet_push_is_checked_when_given(make_ctx, tmp_path, fake_gspread):
    path = _sa_file(tmp_path)
    ctx = ctx_for(make_ctx)
    results = run_doctor(ctx, google_sheet={"spreadsheet_id": "1AbC", "worksheet": "{date}",
                                            "service_account_file": str(path)})
    last = results[-1]
    assert (last.kind, last.type, last.label, last.status) == ("delivery", "google_sheet", "Google Sheet push", OK)
    assert run_doctor(ctx, google_sheet={"spreadsheet_id": ""})[-1].kind != "delivery"


# --- HTTP failure handling + secrets ---------------------------------------------------------------------

def _network_error(call):
    query = "&".join(f"{k}={v}" for k, v in call["params"].items())
    raise HttpError(0, call["url"], "ConnectionError: HTTPSConnectionPool(host='api.example', port=443): Max "
                                    f"retries exceeded with url: /x?{query} (Caused by NewConnectionError)")


def test_network_error_is_could_not_reach_host(make_ctx):
    ctx = ctx_for(make_ctx, env={"MILLIONVERIFIER_API_KEY": "mv-SECRET"})
    ctx.http.add("GET", MV_CREDITS, fn=_network_error)
    res = one(ctx, "verifier", {"type": "millionverifier"})
    assert res.status == FAILED
    assert res.detail.startswith("could not reach api.millionverifier.com (ConnectionError)")
    assert "mv-SECRET" not in res.detail


def test_real_http_client_network_error_is_redacted(make_ctx):
    ctx = ctx_for(make_ctx, env={"ZEROBOUNCE_API_KEY": "zb-SECRET"})
    client = HttpClient(retries=0)

    def boom(method, url, **kw):
        raise requests.exceptions.ConnectionError(f"Max retries exceeded with url: {url}?api_key=zb-SECRET")

    client.session.request = boom
    ctx.http = client
    res = one(ctx, "verifier", {"type": "zerobounce"})
    assert res.status == FAILED and "could not reach api.zerobounce.net" in res.detail
    assert "zb-SECRET" not in res.detail


@pytest.mark.parametrize("status, body, needle", [
    (500, {"error": "boom"}, "HTTP 500 from https://api.apollo.io/v1/auth/health: boom"),
    (429, {"message": "Too many requests"}, "rate limited by api.apollo.io (HTTP 429: Too many requests)"),
    (403, "<html>Forbidden</html>", "key rejected (HTTP 403) - check $APOLLO_API_KEY"),
])
def test_http_error_statuses(make_ctx, status, body, needle):
    ctx = ctx_for(make_ctx, env={"APOLLO_API_KEY": "k-apollo"})
    ctx.http.add("GET", APOLLO_HEALTH, status=status, **({"text": body} if isinstance(body, str) else {"json": body}))
    res = one(ctx, "source", {"type": "apollo"})
    assert res.status == FAILED and needle in res.detail


def test_non_json_answer(make_ctx):
    ctx = ctx_for(make_ctx, env={"APOLLO_API_KEY": "k-apollo"})
    ctx.http.add("GET", APOLLO_HEALTH, text="<html>maintenance</html>")
    res = one(ctx, "source", {"type": "apollo"})
    assert res.status == FAILED and "not JSON" in res.detail


def test_keys_never_leak_even_when_providers_echo_them(make_ctx):
    keys = {"APOLLO_API_KEY": "LEAK-apollo-111", "HUNTER_API_KEY": "LEAK-hunter-222",
            "ZEROBOUNCE_API_KEY": "LEAK-zb-333", "THEIRSTACK_API_KEY": "LEAK-ts-444",
            "APIFY_TOKEN": "LEAK-apify-555", "ANTHROPIC_API_KEY": "LEAK-ant-666",
            "ADZUNA_APP_ID": "LEAK-azid-777", "ADZUNA_APP_KEY": "LEAK-azkey-888"}
    ctx = ctx_for(make_ctx, env=keys,
                  sources=[{"type": "apollo"}, {"type": "theirstack"}, {"type": "apify", "actor": "a/b"},
                           {"type": "adzuna", "countries": ["gb"]}],
                  enrichment={"finders": [{"type": "hunter"}], "verifier": {"type": "zerobounce"}},
                  writer={"provider": "anthropic", "model": "claude-sonnet-5"})

    def echo(call):
        blob = json.dumps({"params": call["params"], "headers": call["headers"]})
        return 401, {"error": {"message": f"bad credentials {blob}"}}

    ctx.http.add("GET", "https://", fn=echo)
    results = run_doctor(ctx)
    text = " ".join(r.detail + " " + r.quota for r in results)
    assert all(r.status == FAILED for r in results if r.kind in ("source", "finder", "llm"))
    for value in keys.values():
        assert value not in text


def test_malformed_entry_is_reported_without_echoing_it(make_ctx):
    ctx = ctx_for(make_ctx)
    res = Doctor(ctx).entry("source", {"api_key": "LEAK-123"})
    assert res.status == FAILED and "LEAK-123" not in res.detail and "'type'" in res.detail
    assert Doctor(ctx).entry("source", "apollo").status == FAILED


def test_unknown_type_fails_clearly(make_ctx):
    ctx = ctx_for(make_ctx)
    res = one(ctx, "source", {"type": "no_such_source"})
    assert res.status == FAILED and "unknown source type 'no_such_source'" in res.detail


# --- dry run ---------------------------------------------------------------------------------------------

def test_dry_run_contacts_nobody_but_still_checks_keys(make_ctx):
    ctx = ctx_for(make_ctx, env={"APOLLO_API_KEY": "k-apollo", "ANTHROPIC_API_KEY": "k-ant"}, dry_run=True,
                  sources=[{"type": "apollo"}, {"type": "theirstack"}],
                  writer={"provider": "anthropic", "model": "claude-sonnet-5"})
    results = run_doctor(ctx)
    apollo = find(results, "source", "apollo")[0]
    assert apollo.status == SKIPPED and apollo.detail == "dry run - key found, api.apollo.io not contacted"
    assert find(results, "source", "theirstack")[0].status == MISSING_KEY
    assert find(results, "llm", "anthropic")[0].status == SKIPPED
    assert ctx.http.calls == []


# --- plugins / extension points ------------------------------------------------------------------------

class PluginSource(Source):
    name = "doctor_plugin"
    env_key = "PLUGIN_API_KEY"

    def fetch(self):  # pragma: no cover - never called by the doctor
        return []


class OfflinePluginSource(Source):
    name = "doctor_offline_plugin"
    offline = True

    def fetch(self):  # pragma: no cover
        return []


@pytest.fixture
def plugins():
    registry.register("source", "doctor_plugin", "tests.test_doctor:PluginSource")
    registry.register("source", "doctor_offline_plugin", "tests.test_doctor:OfflinePluginSource")
    yield
    registry._REGISTRY["source"].pop("doctor_plugin", None)
    registry._REGISTRY["source"].pop("doctor_offline_plugin", None)
    CHECKS.pop(("source", "doctor_plugin"), None)


def test_plugin_adapters_without_a_check(make_ctx, plugins):
    ctx = ctx_for(make_ctx, env={})
    res = one(ctx, "source", {"type": "doctor_plugin"})
    assert res.status == MISSING_KEY and "$PLUGIN_API_KEY" in res.detail
    ctx2 = ctx_for(make_ctx, env={"PLUGIN_API_KEY": "k"})
    res2 = one(ctx2, "source", {"type": "doctor_plugin"})
    assert res2.status == SKIPPED and "no live check available" in res2.detail
    assert one(ctx2, "source", {"type": "doctor_offline_plugin"}).detail == "no key needed"


def test_plugin_can_register_a_check_and_crashes_are_contained(make_ctx, plugins):
    ctx = ctx_for(make_ctx, env={"PLUGIN_API_KEY": "plug-SECRET"})

    def check(doc, adapter):
        key = doc.key(adapter)
        raise RuntimeError(f"bug while using {key}")

    CHECKS[("source", "doctor_plugin")] = check
    res = one(ctx, "source", {"type": "doctor_plugin"})
    assert res.status == FAILED and "check failed unexpectedly (RuntimeError" in res.detail
    assert "plug-SECRET" not in res.detail

    CHECKS[("source", "doctor_plugin")] = lambda doc, adapter: doctor_mod.result(adapter, OK, "fine", "7 credits")
    res2 = one(ctx, "source", {"type": "doctor_plugin"})
    assert (res2.status, res2.quota) == (OK, "7 credits")


# --- result helpers --------------------------------------------------------------------------------------

def test_result_lines_and_dict():
    ok = DoctorResult("verifier", "millionverifier", "millionverifier", OK, "key accepted", "9,950 credits left")
    bad = DoctorResult("source", "apollo", "Apollo US", MISSING_KEY, "missing key - set $APOLLO_API_KEY")
    assert ok.line() == "[ok]   verifier millionverifier: key accepted (9,950 credits left)"
    assert bad.line() == "[MISS] source apollo 'Apollo US': missing key - set $APOLLO_API_KEY"
    assert not ok.problem and bad.problem
    assert ok.to_dict() == {"kind": "verifier", "type": "millionverifier", "label": "millionverifier",
                            "status": "ok", "detail": "key accepted", "quota": "9,950 credits left"}
    lines = format_results([ok, bad])
    assert lines[-1] == "1 ok, 0 failed, 1 missing key, 0 skipped"


@pytest.mark.parametrize("value, expected", [
    (1250, "1,250"), (1250.0, "1,250"), ("1,250", "1,250"), (12.5, "12.5"), ("7", "7"), (0.25, "0.25"),
])
def test_number_formatting(value, expected):
    assert doctor_mod.fmt(doctor_mod.number(value)) == expected


@pytest.mark.parametrize("value", [None, True, "n/a", ""])
def test_number_rejects_non_numbers(value):
    assert doctor_mod.number(value) is None
