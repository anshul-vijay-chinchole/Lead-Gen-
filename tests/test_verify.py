"""Tests for the email verifiers (basic, MillionVerifier, ZeroBounce, NeverBounce, Hunter)."""
from __future__ import annotations

import pytest

from leadgen import registry
from leadgen.context import MissingCredentialError
from leadgen.http import HttpError
from leadgen.models import EmailStatus
from leadgen.verify import VerificationResult, VerifierError
from leadgen.verify.basic import (DISPOSABLE_DOMAINS, BasicVerifier, is_disposable, normalize_email,
                                  precheck)
from leadgen.verify.hunter import HunterVerifier
from leadgen.verify.millionverifier import MillionVerifier
from leadgen.verify.neverbounce import NeverBounceVerifier
from leadgen.verify.zerobounce import ZeroBounceVerifier

MV_URL = "https://api.millionverifier.com/api/v3/"
ZB_URL = "https://api.zerobounce.net/v2/validate"
NB_URL = "https://api.neverbounce.com/v4/single/check"
HUNTER_URL = "https://api.hunter.io/v2/email-verifier"


# --- canned payloads (documented response shapes) ----------------------------------------

def mv_payload(result="ok", **over):
    body = {"email": "jane@acme.com", "quality": "good", "result": result, "resultcode": 1,
            "subresult": result, "free": False, "role": False, "didyoumean": "", "credits": 9950,
            "executiontime": 1, "error": "", "livemode": True}
    body.update(over)
    return body


def zb_payload(status="valid", **over):
    body = {"address": "jane@acme.com", "status": status, "sub_status": "", "free_email": False,
            "did_you_mean": None, "account": "jane", "domain": "acme.com", "domain_age_days": "9000",
            "smtp_provider": "google", "mx_found": "true", "mx_record": "aspmx.l.google.com",
            "firstname": "Jane", "lastname": "Doe", "gender": "female", "country": None, "region": None,
            "city": None, "zipcode": None, "processed_at": "2026-09-24 10:00:00.000"}
    body.update(over)
    return body


def nb_payload(result="valid", **over):
    body = {"status": "success", "result": result,
            "flags": ["has_dns", "has_dns_mx", "smtp_connectable"],
            "suggested_correction": "", "execution_time": 285}
    body.update(over)
    return body


def hunter_payload(status="valid", result="deliverable", score=91):
    return {"data": {"status": status, "result": result, "score": score, "email": "jane@acme.com",
                     "regexp": True, "gibberish": False, "disposable": status == "disposable",
                     "webmail": status == "webmail", "mx_records": True, "smtp_server": True,
                     "smtp_check": True, "accept_all": status == "accept_all", "block": False,
                     "sources": []},
            "meta": {"params": {"email": "jane@acme.com"}}}


# --- helpers ------------------------------------------------------------------------------

def test_normalize_and_disposable_helpers():
    assert normalize_email("  Jane@ACME.com ") == "jane@acme.com"
    assert normalize_email(None) == ""
    assert len(DISPOSABLE_DOMAINS) >= 40
    assert is_disposable("x@mailinator.com")
    assert is_disposable("x@inbox.mailinator.com")  # subdomain of a disposable domain
    assert is_disposable("X@YopMail.com")
    assert not is_disposable("jane@acme.com")
    assert not is_disposable("not-an-email")
    assert not is_disposable("")
    assert is_disposable("x@burner.example", extra=["burner.example"])
    assert precheck("jane@acme.com", "p") is None
    bad = precheck("jane@", "p")
    assert bad.status == EmailStatus.INVALID and bad.raw_status == "syntax_error" and bad.provider == "p"
    assert precheck("x@yopmail.com", "p").raw_status == "disposable"
    assert precheck("x@yopmail.com", "p", check_disposable=False) is None


# --- basic --------------------------------------------------------------------------------

@pytest.mark.parametrize("email", ["", "   ", "jane", "jane@", "@acme.com", "jane@acme", "jane doe@acme.com",
                                   "jane@@acme.com", None])
def test_basic_rejects_bad_syntax(make_ctx, email):
    res = BasicVerifier({}, make_ctx()).verify(email)
    assert res.status == EmailStatus.INVALID
    assert res.raw_status == "syntax_error"
    assert res.provider == "basic"


@pytest.mark.parametrize("domain", ["mailinator.com", "10minutemail.com", "guerrillamail.com", "yopmail.com",
                                    "tempmail.com", "temp-mail.org", "sharklasers.com", "trashmail.com"])
def test_basic_rejects_disposable(make_ctx, domain):
    res = BasicVerifier({}, make_ctx()).verify(f"someone@{domain}")
    assert res.status == EmailStatus.INVALID and res.raw_status == "disposable"


def test_basic_ok_is_unknown_and_offline(make_ctx):
    ctx = make_ctx()
    v = BasicVerifier({}, ctx)
    assert v.offline is True and v.name == "basic"
    res = v.verify("  Jane.Doe@Acme.COM ")
    assert isinstance(res, VerificationResult)
    assert res.email == "jane.doe@acme.com"
    assert res.status == EmailStatus.UNKNOWN and res.raw_status == "syntax_ok" and res.provider == "basic"
    assert ctx.http.calls == []


def test_basic_extra_disposable_domains_and_verify_many(make_ctx):
    v = BasicVerifier({"extra_disposable_domains": ["burner.example"]}, make_ctx())
    out = v.verify_many(["a@burner.example", "b@acme.com", "bad"])
    assert [r.status for r in out] == [EmailStatus.INVALID, EmailStatus.UNKNOWN, EmailStatus.INVALID]
    v2 = BasicVerifier({"extra_disposable_domains": "burner.example, other.example"}, make_ctx())
    assert v2.verify("x@other.example").raw_status == "disposable"


def test_registry_builds_every_verifier(make_ctx):
    ctx = make_ctx()
    expected = {"basic": BasicVerifier, "millionverifier": MillionVerifier, "zerobounce": ZeroBounceVerifier,
                "neverbounce": NeverBounceVerifier, "hunter": HunterVerifier}
    for type_, cls in expected.items():
        v = registry.create("verifier", {"type": type_}, ctx)  # no key needed to instantiate
        assert isinstance(v, cls) and v.name == type_
        assert v.offline is (type_ == "basic")


# --- shared API-verifier behaviour ---------------------------------------------------------

API_VERIFIERS = [
    (MillionVerifier, "MILLIONVERIFIER_API_KEY"),
    (ZeroBounceVerifier, "ZEROBOUNCE_API_KEY"),
    (NeverBounceVerifier, "NEVERBOUNCE_API_KEY"),
    (HunterVerifier, "HUNTER_API_KEY"),
]


@pytest.mark.parametrize("cls,env", API_VERIFIERS)
def test_api_verifiers_short_circuit_bad_syntax_without_http(make_ctx, cls, env):
    ctx = make_ctx(env={env: "k"})
    res = cls({}, ctx).verify("not-an-email")
    assert res.status == EmailStatus.INVALID and res.raw_status == "syntax_error" and res.provider == cls.name
    assert ctx.http.calls == []


@pytest.mark.parametrize("cls,env", API_VERIFIERS)
def test_api_verifiers_short_circuit_disposable_without_http(make_ctx, cls, env):
    ctx = make_ctx(env={env: "k"})
    res = cls({}, ctx).verify("x@mailinator.com")
    assert res.status == EmailStatus.INVALID and res.raw_status == "disposable"
    assert ctx.http.calls == []


@pytest.mark.parametrize("cls,env", API_VERIFIERS)
def test_api_verifiers_bad_syntax_needs_no_credentials(make_ctx, cls, env):
    ctx = make_ctx(env={})
    assert cls({}, ctx).verify("nope@").status == EmailStatus.INVALID


@pytest.mark.parametrize("cls,env", API_VERIFIERS)
def test_api_verifiers_missing_credential(make_ctx, cls, env):
    ctx = make_ctx(env={})
    v = cls({}, ctx)  # instantiating never needs the key
    with pytest.raises(MissingCredentialError) as ei:
        v.verify("jane@acme.com")
    assert env in str(ei.value)
    assert ctx.http.calls == []


@pytest.mark.parametrize("cls,env", API_VERIFIERS)
def test_api_verifiers_dry_run_never_call_provider(make_ctx, cls, env):
    ctx = make_ctx(env={env: "k"}, dry_run=True)
    res = cls({}, ctx).verify("jane@acme.com")
    assert res.status == EmailStatus.UNKNOWN and res.raw_status == "dry_run" and res.provider == cls.name
    assert ctx.http.calls == []


# --- MillionVerifier ------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("ok", EmailStatus.VALID), ("catch_all", EmailStatus.RISKY), ("unknown", EmailStatus.UNKNOWN),
    ("error", EmailStatus.UNKNOWN), ("disposable", EmailStatus.INVALID), ("invalid", EmailStatus.INVALID),
    ("something_new", EmailStatus.UNKNOWN), ("OK", EmailStatus.VALID),
])
def test_millionverifier_result_mapping(make_ctx, raw, expected):
    ctx = make_ctx(env={"MILLIONVERIFIER_API_KEY": "mv-key"})
    ctx.http.add("GET", MV_URL, json=mv_payload(raw))
    res = MillionVerifier({}, ctx).verify("Jane@Acme.com")
    assert res.status == expected
    assert res.raw_status == raw.lower() and res.provider == "millionverifier" and res.email == "jane@acme.com"


def test_millionverifier_request_shape(make_ctx):
    ctx = make_ctx(env={"MILLIONVERIFIER_API_KEY": "mv-key"})
    ctx.http.add("GET", MV_URL, json=mv_payload("ok"))
    MillionVerifier({}, ctx).verify("jane@acme.com")
    call = ctx.http.calls[0]
    assert call["method"] == "GET" and call["url"] == MV_URL
    assert call["params"] == {"api": "mv-key", "email": "jane@acme.com", "timeout": 10}


def test_millionverifier_config_timeout_key_and_details(make_ctx):
    ctx = make_ctx(env={})
    ctx.http.add("GET", MV_URL, json=mv_payload("ok", role=True, free=True, didyoumean="jane@acme.co",
                                                 subresult="ok", quality="risky"))
    res = MillionVerifier({"api_key": "cfg-key", "timeout": 30}, ctx).verify("jane@acme.com")
    assert ctx.http.calls[0]["params"]["api"] == "cfg-key"
    assert ctx.http.calls[0]["params"]["timeout"] == 30
    assert "role address" in res.detail and "did you mean jane@acme.co" in res.detail
    assert "quality=risky" in res.detail


def test_millionverifier_catch_all_subresult_in_detail(make_ctx):
    ctx = make_ctx(env={"MILLIONVERIFIER_API_KEY": "k"})
    ctx.http.add("GET", MV_URL, json=mv_payload("invalid", subresult="mailbox_not_found"))
    res = MillionVerifier({}, ctx).verify("jane@acme.com")
    assert res.status == EmailStatus.INVALID and "subresult=mailbox_not_found" in res.detail


@pytest.mark.parametrize("err", ["Invalid API key", "Insufficient credits"])
def test_millionverifier_error_field_raises(make_ctx, err):
    ctx = make_ctx(env={"MILLIONVERIFIER_API_KEY": "k"})
    ctx.http.add("GET", MV_URL, json={"email": "jane@acme.com", "result": "", "error": err, "credits": 0})
    with pytest.raises(VerifierError, match=err):
        MillionVerifier({}, ctx).verify("jane@acme.com")


def test_millionverifier_malformed_responses_raise(make_ctx):
    ctx = make_ctx(env={"MILLIONVERIFIER_API_KEY": "k"})
    ctx.http.add("GET", MV_URL, json=["unexpected"], times=1)
    ctx.http.add("GET", MV_URL, json={"email": "jane@acme.com", "error": ""}, times=1)
    v = MillionVerifier({}, ctx)
    with pytest.raises(VerifierError, match="unexpected response"):
        v.verify("jane@acme.com")
    with pytest.raises(VerifierError, match="no 'result'"):
        v.verify("jane@acme.com")


def test_millionverifier_http_error_propagates(make_ctx):
    ctx = make_ctx(env={"MILLIONVERIFIER_API_KEY": "k"})
    ctx.http.add("GET", MV_URL, status=500, json={"error": "boom"})
    with pytest.raises(HttpError) as ei:
        MillionVerifier({}, ctx).verify("jane@acme.com")
    assert ei.value.status == 500


def test_millionverifier_bad_timeout_config(make_ctx):
    ctx = make_ctx(env={"MILLIONVERIFIER_API_KEY": "k"})
    with pytest.raises(ValueError, match="timeout"):
        MillionVerifier({"timeout": "soon"}, ctx).verify("jane@acme.com")


def test_millionverifier_precheck_disposable_can_be_disabled(make_ctx):
    ctx = make_ctx(env={"MILLIONVERIFIER_API_KEY": "k"})
    ctx.http.add("GET", MV_URL, json=mv_payload("disposable", email="x@mailinator.com"))
    res = MillionVerifier({"precheck_disposable": False}, ctx).verify("x@mailinator.com")
    assert len(ctx.http.calls) == 1
    assert res.status == EmailStatus.INVALID and res.raw_status == "disposable"


# --- ZeroBounce -----------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("valid", EmailStatus.VALID), ("catch-all", EmailStatus.RISKY), ("unknown", EmailStatus.UNKNOWN),
    ("invalid", EmailStatus.INVALID), ("spamtrap", EmailStatus.INVALID), ("abuse", EmailStatus.INVALID),
    ("do_not_mail", EmailStatus.INVALID), ("brand_new_status", EmailStatus.UNKNOWN),
])
def test_zerobounce_status_mapping(make_ctx, raw, expected):
    ctx = make_ctx(env={"ZEROBOUNCE_API_KEY": "zb-key"})
    ctx.http.add("GET", ZB_URL, json=zb_payload(raw))
    res = ZeroBounceVerifier({}, ctx).verify("jane@acme.com")
    assert res.status == expected and res.raw_status == raw and res.provider == "zerobounce"


def test_zerobounce_request_shape(make_ctx):
    ctx = make_ctx(env={"ZEROBOUNCE_API_KEY": "zb-key"})
    ctx.http.add("GET", ZB_URL, json=zb_payload("valid"))
    ZeroBounceVerifier({}, ctx).verify("jane@acme.com")
    call = ctx.http.calls[0]
    assert call["url"] == ZB_URL
    assert call["params"] == {"api_key": "zb-key", "email": "jane@acme.com", "ip_address": ""}


def test_zerobounce_regional_base_url_timeout_and_detail(make_ctx):
    ctx = make_ctx(env={"ZEROBOUNCE_API_KEY": "zb-key"})
    url = "https://api-eu.zerobounce.net/v2/validate"
    ctx.http.add("GET", url, json=zb_payload("do_not_mail", sub_status="role_based", free_email=True,
                                              did_you_mean="jane@acme.co"))
    res = ZeroBounceVerifier({"base_url": "https://api-eu.zerobounce.net/v2/", "timeout": 20}, ctx) \
        .verify("jane@acme.com")
    call = ctx.http.calls[0]
    assert call["url"] == url and call["params"]["timeout"] == 20
    assert res.status == EmailStatus.INVALID
    assert "sub_status=role_based" in res.detail and "did you mean jane@acme.co" in res.detail
    assert "free email provider" in res.detail


def test_zerobounce_error_payload_raises(make_ctx):
    ctx = make_ctx(env={"ZEROBOUNCE_API_KEY": "bad"})
    ctx.http.add("GET", ZB_URL, json={"error": "Invalid API key or your account ran out of credits"})
    with pytest.raises(VerifierError, match="ran out of credits"):
        ZeroBounceVerifier({}, ctx).verify("jane@acme.com")


def test_zerobounce_missing_status_and_non_dict_raise(make_ctx):
    ctx = make_ctx(env={"ZEROBOUNCE_API_KEY": "k"})
    ctx.http.add("GET", ZB_URL, json={"address": "jane@acme.com"}, times=1)
    ctx.http.add("GET", ZB_URL, text="", times=1)
    v = ZeroBounceVerifier({}, ctx)
    with pytest.raises(VerifierError, match="no 'status'"):
        v.verify("jane@acme.com")
    with pytest.raises(VerifierError, match="unexpected response"):
        v.verify("jane@acme.com")


def test_zerobounce_http_400_propagates(make_ctx):
    ctx = make_ctx(env={"ZEROBOUNCE_API_KEY": "k"})
    ctx.http.add("GET", ZB_URL, status=400, json={"error": "Missing parameter"})
    with pytest.raises(HttpError):
        ZeroBounceVerifier({}, ctx).verify("jane@acme.com")


# --- NeverBounce ----------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("valid", EmailStatus.VALID), ("invalid", EmailStatus.INVALID), ("disposable", EmailStatus.INVALID),
    ("catchall", EmailStatus.RISKY), ("unknown", EmailStatus.UNKNOWN), ("mystery", EmailStatus.UNKNOWN),
])
def test_neverbounce_result_mapping(make_ctx, raw, expected):
    ctx = make_ctx(env={"NEVERBOUNCE_API_KEY": "nb-key"})
    ctx.http.add("GET", NB_URL, json=nb_payload(raw))
    res = NeverBounceVerifier({}, ctx).verify("jane@acme.com")
    assert res.status == expected and res.raw_status == raw and res.provider == "neverbounce"


@pytest.mark.parametrize("code,expected_raw,expected", [
    (0, "valid", EmailStatus.VALID), (1, "invalid", EmailStatus.INVALID),
    (2, "disposable", EmailStatus.INVALID), (3, "catchall", EmailStatus.RISKY), (4, "unknown", EmailStatus.UNKNOWN),
])
def test_neverbounce_numeric_result_codes(make_ctx, code, expected_raw, expected):
    ctx = make_ctx(env={"NEVERBOUNCE_API_KEY": "nb-key"})
    ctx.http.add("GET", NB_URL, json=nb_payload(code))
    res = NeverBounceVerifier({}, ctx).verify("jane@acme.com")
    assert res.raw_status == expected_raw and res.status == expected


def test_neverbounce_request_shape_and_detail(make_ctx):
    ctx = make_ctx(env={"NEVERBOUNCE_API_KEY": "nb-key"})
    ctx.http.add("GET", NB_URL, json=nb_payload("valid", suggested_correction="jane@acme.co",
                                                 flags=["has_dns", "free_email_host", "role_account"]))
    res = NeverBounceVerifier({}, ctx).verify("jane@acme.com")
    call = ctx.http.calls[0]
    assert call["url"] == NB_URL and call["params"] == {"key": "nb-key", "email": "jane@acme.com"}
    assert "did you mean jane@acme.co" in res.detail
    assert "free_email_host" in res.detail and "role_account" in res.detail and "has_dns" not in res.detail


def test_neverbounce_custom_version_and_timeout(make_ctx):
    ctx = make_ctx(env={"NEVERBOUNCE_API_KEY": "nb-key"})
    ctx.http.add("GET", "https://api.neverbounce.com/v4.2/single/check", json=nb_payload("valid"))
    NeverBounceVerifier({"base_url": "https://api.neverbounce.com/v4.2", "timeout": 15}, ctx).verify("jane@acme.com")
    assert ctx.http.calls[0]["params"]["timeout"] == 15


@pytest.mark.parametrize("payload,match", [
    ({"status": "auth_failure", "message": "Invalid API key"}, "auth_failure: Invalid API key"),
    ({"status": "throttle_triggered", "message": "Too many requests"}, "throttle_triggered"),
    ({"result": "valid"}, "missing status"),
    ({"status": "success"}, "no 'result'"),
])
def test_neverbounce_failures_raise(make_ctx, payload, match):
    ctx = make_ctx(env={"NEVERBOUNCE_API_KEY": "k"})
    ctx.http.add("GET", NB_URL, json=payload)
    with pytest.raises(VerifierError, match=match):
        NeverBounceVerifier({}, ctx).verify("jane@acme.com")


# --- Hunter ---------------------------------------------------------------------------------

@pytest.mark.parametrize("status,result,expected", [
    ("valid", "deliverable", EmailStatus.VALID),
    ("accept_all", "risky", EmailStatus.RISKY),
    ("webmail", "deliverable", EmailStatus.VALID),
    ("webmail", "risky", EmailStatus.UNKNOWN),
    ("disposable", "risky", EmailStatus.INVALID),
    ("invalid", "undeliverable", EmailStatus.INVALID),
    ("unknown", "risky", EmailStatus.UNKNOWN),
    ("new_thing", "", EmailStatus.UNKNOWN),
])
def test_hunter_status_mapping(make_ctx, status, result, expected):
    ctx = make_ctx(env={"HUNTER_API_KEY": "h-key"})
    ctx.http.add("GET", HUNTER_URL, json=hunter_payload(status, result))
    res = HunterVerifier({}, ctx).verify("jane@acme.com")
    assert res.status == expected and res.raw_status == status and res.provider == "hunter"


def test_hunter_request_shape_and_detail(make_ctx):
    ctx = make_ctx(env={"HUNTER_API_KEY": "h-key"})
    ctx.http.add("GET", HUNTER_URL, json=hunter_payload("valid", "deliverable", 97))
    res = HunterVerifier({}, ctx).verify("Jane@Acme.com")
    call = ctx.http.calls[0]
    assert call["url"] == HUNTER_URL and call["params"] == {"email": "jane@acme.com", "api_key": "h-key"}
    assert "score=97" in res.detail and "result=deliverable" in res.detail


def test_hunter_legacy_result_only(make_ctx):
    ctx = make_ctx(env={"HUNTER_API_KEY": "h-key"})
    ctx.http.add("GET", HUNTER_URL, json={"data": {"result": "undeliverable", "score": 10}})
    res = HunterVerifier({}, ctx).verify("jane@acme.com")
    assert res.status == EmailStatus.INVALID and res.raw_status == "undeliverable"


@pytest.mark.parametrize("http_status,raw", [(202, "pending"), (222, "smtp_error"), (451, "unavailable")])
def test_hunter_no_verdict_statuses_are_unknown(make_ctx, http_status, raw):
    ctx = make_ctx(env={"HUNTER_API_KEY": "h-key"})
    ctx.http.add("GET", HUNTER_URL, status=http_status, json={"data": None} if http_status != 451 else
                 {"errors": [{"id": "claimed_email", "code": 451, "details": "..."}]})
    res = HunterVerifier({}, ctx).verify("jane@acme.com")
    assert res.status == EmailStatus.UNKNOWN and res.raw_status == raw and res.detail


@pytest.mark.parametrize("http_status", [400, 401, 403, 429, 500])
def test_hunter_http_errors_raise(make_ctx, http_status):
    ctx = make_ctx(env={"HUNTER_API_KEY": "h-key"})
    ctx.http.add("GET", HUNTER_URL, status=http_status,
                 json={"errors": [{"id": "some_error", "code": http_status, "details": "nope"}]})
    with pytest.raises(HttpError) as ei:
        HunterVerifier({}, ctx).verify("jane@acme.com")
    assert ei.value.status == http_status
    assert "h-key" not in str(ei.value)


def test_hunter_malformed_bodies_raise(make_ctx):
    ctx = make_ctx(env={"HUNTER_API_KEY": "h-key"})
    ctx.http.add("GET", HUNTER_URL, json={"meta": {}}, times=1)
    ctx.http.add("GET", HUNTER_URL, text="<html>oops</html>", times=1)
    ctx.http.add("GET", HUNTER_URL, json={"errors": [{"id": "x", "details": "weird"}]}, times=1)
    v = HunterVerifier({}, ctx)
    with pytest.raises(VerifierError, match="no 'data'"):
        v.verify("jane@acme.com")
    with pytest.raises(VerifierError, match="not JSON"):
        v.verify("jane@acme.com")
    with pytest.raises(VerifierError, match="weird"):
        v.verify("jane@acme.com")


def test_verifier_config_api_key_env_override(make_ctx):
    ctx = make_ctx(env={"MY_HUNTER": "other-key"})
    ctx.http.add("GET", HUNTER_URL, json=hunter_payload())
    HunterVerifier({"api_key_env": "MY_HUNTER"}, ctx).verify("jane@acme.com")
    assert ctx.http.calls[0]["params"]["api_key"] == "other-key"
