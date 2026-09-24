"""Webhook server tests: a real server on a free port, hit with urllib."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

import pytest

from leadgen import server as server_mod
from leadgen.models import Company, Contact, EmailStatus, Lead, ReplyCategory, Stage
from leadgen.server import MAX_BODY_BYTES, make_server

TOKEN = "s3cret-token"
OFFER = {"sender_name": "Alex Morgan", "sender_company": "Northbeam Talent",
         "sender_website": "https://northbeam-demo.com", "booking_link": "https://northbeam-demo.com/book"}


def seed_lead(ctx, email: str = "jane@acme-demo.com") -> Lead:
    company = Company(name="Acme", domain="acme-demo.com")
    contact = Contact(full_name="Jane Doe", title="CFO", email=email, email_status=EmailStatus.VALID)
    lead = Lead(company=company, contact=contact, playbook=ctx.playbook.name, run_id="r1",
                stage=Stage.READY, score=85, tier="hot")
    ctx.store.save_lead(lead)
    ctx.store.mark_exported(lead.id, "instantly")
    return lead


class Running:
    def __init__(self, ctx, token: Optional[str]):
        self.ctx = ctx
        self.httpd = make_server(ctx, "127.0.0.1", 0, token=token)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05},
                                       daemon=True)
        self.thread.start()

    def request(self, method: str, path: str, body: Any = None, headers: Optional[Dict[str, str]] = None,
                raw: Optional[bytes] = None) -> Tuple[int, Dict[str, Any], Dict[str, str]]:
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode("utf-8"))
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read() or b"null"), dict(resp.headers)
        except urllib.error.HTTPError as e:
            payload = e.read()
            return e.code, (json.loads(payload) if payload else {}), dict(e.headers)

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def serve(make_ctx):
    started = []

    def _start(token: Optional[str] = TOKEN, **overrides: Any) -> Running:
        overrides.setdefault("offer", OFFER)
        overrides.setdefault("replies", {"classifier": "rules"})
        ctx = make_ctx(**overrides)
        r = Running(ctx, token)
        started.append(r)
        return r

    yield _start
    for r in started:
        r.close()


def instantly_payload(email: str = "jane@acme-demo.com", text: str = "Sounds good - send me some times.",
                      **extra: Any) -> Dict[str, Any]:
    p = {"event_type": "reply_received", "timestamp": "2026-09-24T10:00:00.000Z", "campaign_id": "c-1",
         "campaign_name": "Finance hiring", "lead_email": email, "email_account": "alex@northbeam-demo.com",
         "reply_subject": "Re: finance manager at Acme", "reply_text": text,
         "reply_html": f"<div>{text}</div>"}
    p.update(extra)
    return p


def test_health_needs_no_token(serve):
    s = serve()
    status, body, headers = s.request("GET", "/health")
    assert status == 200 and body == {"ok": True, "playbook": "test"}
    assert headers["Content-Type"].startswith("application/json")
    status, _, _ = s.request("GET", "/health/")          # trailing slash tolerated
    assert status == 200


def test_instantly_reply_is_classified_and_acted_on(serve, capsys):
    s = serve()
    lead = seed_lead(s.ctx)
    status, body, _ = s.request("POST", "/webhook/instantly", instantly_payload(),
                                headers={"X-Leadgen-Token": TOKEN})
    assert status == 200
    assert body["ok"] is True and body["category"] == ReplyCategory.POSITIVE
    assert body["lead_id"] == lead.id and body["from_email"] == "jane@acme-demo.com"
    assert "lead stage -> positive" in body["action"]
    assert s.ctx.store.get_lead(lead.id).stage == Stage.POSITIVE
    saved = s.ctx.store.list_replies("test")
    assert len(saved) == 1 and saved[0].category == ReplyCategory.POSITIVE
    assert "[POSITIVE]" in capsys.readouterr().out       # console alert fired


def test_token_in_query_string_and_duplicate_delivery(serve):
    s = serve()
    lead = seed_lead(s.ctx)
    payload = instantly_payload(text="Not interested, thanks.")
    status, body, _ = s.request("POST", f"/webhook?token={TOKEN}", payload)
    assert status == 200 and body["category"] == ReplyCategory.NEGATIVE
    assert s.ctx.store.get_lead(lead.id).stage == Stage.LOST
    assert s.ctx.store.is_suppressed(email="jane@acme-demo.com")
    # providers retry: the same event again is recognised and skipped
    status, body, _ = s.request("POST", f"/webhook?token={TOKEN}", payload)
    assert status == 200 and "duplicate" in body["action"]
    assert len(s.ctx.store.list_replies("test")) == 1


def test_smartlead_and_generic_shapes(serve):
    s = serve()
    seed_lead(s.ctx)
    smartlead = {"event_type": "EMAIL_REPLY", "sl_lead_email": "jane@acme-demo.com",
                 "subject": "Re: finance manager at Acme", "time_replied": "2026-09-24T11:00:00Z",
                 "reply_message": {"text": "What are your fees?", "html": "<p>What are your fees?</p>",
                                   "time": "2026-09-24T11:00:00Z"}}
    status, body, _ = s.request("POST", "/webhook/smartlead", smartlead, headers={"X-Leadgen-Token": TOKEN})
    assert status == 200 and body["category"] == ReplyCategory.QUESTION
    generic = {"from_email": "someone@unknown-demo.com", "subject": "Re: hello",
               "body": "Please remove me from your list.", "received_at": "2026-09-24T12:00:00Z"}
    status, body, _ = s.request("POST", "/webhook/reply", generic, headers={"X-Leadgen-Token": TOKEN})
    assert status == 200 and body["category"] == ReplyCategory.UNSUBSCRIBE
    assert body["lead_id"] is None and "no matching lead" in body["action"]
    assert s.ctx.store.is_suppressed(email="someone@unknown-demo.com")


def test_non_reply_events_are_ignored(serve):
    s = serve()
    for event in ({"event_type": "email_opened", "lead_email": "jane@acme-demo.com"},
                  {"event_type": "EMAIL_SENT", "sl_lead_email": "jane@acme-demo.com"},
                  {"hello": "world"}):
        status, body, _ = s.request("POST", "/webhook", event, headers={"X-Leadgen-Token": TOKEN})
        assert status == 200 and body == {"ok": True, "ignored": True}
    assert s.ctx.store.list_replies() == []


def test_bounce_event_suppresses_the_address(serve):
    s = serve()
    lead = seed_lead(s.ctx)
    status, body, _ = s.request("POST", "/webhook/instantly",
                                {"event_type": "email_bounced", "lead_email": "jane@acme-demo.com",
                                 "timestamp": "2026-09-24T09:00:00Z"}, headers={"X-Leadgen-Token": TOKEN})
    assert status == 200 and body["category"] == ReplyCategory.BOUNCE
    assert s.ctx.store.get_lead(lead.id).stage == Stage.LOST
    assert s.ctx.store.get_verification("jane@acme-demo.com") == EmailStatus.INVALID


def test_json_array_of_events(serve):
    s = serve()
    seed_lead(s.ctx)
    events = [instantly_payload(), {"event_type": "email_opened", "lead_email": "jane@acme-demo.com"}]
    status, body, _ = s.request("POST", "/webhook", events, headers={"X-Leadgen-Token": TOKEN})
    assert status == 200 and body["ok"] is True
    assert body["results"][0]["category"] == ReplyCategory.POSITIVE
    assert body["results"][1] == {"ok": True, "ignored": True}


@pytest.mark.parametrize("headers,path", [
    ({}, "/webhook"),
    ({"X-Leadgen-Token": "wrong"}, "/webhook"),
    ({}, "/webhook?token=wrong"),
    ({}, "/webhook?token="),
])
def test_bad_or_missing_token_is_401(serve, headers, path):
    s = serve()
    status, body, _ = s.request("POST", path, instantly_payload(), headers=headers)
    assert status == 401 and body["ok"] is False and "token" in body["error"]
    assert s.ctx.store.list_replies() == []


def test_no_token_configured_accepts_requests(serve):
    s = serve(token=None)
    status, body, _ = s.request("POST", "/webhook", instantly_payload())
    assert status == 200 and body["category"] == ReplyCategory.POSITIVE


def test_bad_json_and_empty_body_are_400(serve):
    s = serve()
    h = {"X-Leadgen-Token": TOKEN}
    status, body, _ = s.request("POST", "/webhook", raw=b"{not json", headers=h)
    assert status == 400 and "not valid JSON" in body["error"]
    status, body, _ = s.request("POST", "/webhook", raw=b"\xff\xfe\x00", headers=h)
    assert status == 400
    status, body, _ = s.request("POST", "/webhook", raw=b"", headers=h)
    assert status == 400 and "empty body" in body["error"]


def test_body_too_large_is_413(serve):
    s = serve()
    big = b'{"body": "' + b"x" * (MAX_BODY_BYTES + 10) + b'"}'
    status, body, _ = s.request("POST", "/webhook", raw=big, headers={"X-Leadgen-Token": TOKEN})
    assert status == 413 and "too large" in body["error"]
    # the server is still healthy afterwards
    assert s.request("GET", "/health")[0] == 200


def test_unknown_paths_and_wrong_methods(serve):
    s = serve()
    status, _, _ = s.request("GET", "/nope")
    assert status == 404
    status, _, _ = s.request("POST", "/nope", {"a": 1}, headers={"X-Leadgen-Token": TOKEN})
    assert status == 404
    status, _, headers = s.request("GET", "/webhook")
    assert status == 405 and headers.get("Allow") == "POST"
    status, _, headers = s.request("POST", "/health", {"a": 1})
    assert status == 405 and headers.get("Allow") == "GET"


def test_processing_error_is_500_and_server_survives(serve, monkeypatch):
    s = serve()

    def boom(reply, ctx):
        raise RuntimeError("database locked")

    monkeypatch.setattr(server_mod, "handle_reply", boom)
    status, body, _ = s.request("POST", "/webhook", instantly_payload(), headers={"X-Leadgen-Token": TOKEN})
    assert status == 500 and body == {"ok": False, "error": "could not process the event"}
    assert s.request("GET", "/health")[0] == 200


def test_request_logging_is_quiet(serve, capsys):
    s = serve()
    s.request("GET", "/health")
    captured = capsys.readouterr()
    assert "GET /health" not in captured.err and "GET /health" not in captured.out


def test_make_server_needs_a_store(make_ctx):
    ctx = make_ctx()
    store, ctx.store = ctx.store, None
    try:
        with pytest.raises(RuntimeError):
            make_server(ctx, "127.0.0.1", 0)
    finally:
        ctx.store = store


def test_cli_serve_command_starts_and_stops(tmp_path, monkeypatch, capsys):
    """`leadgen serve` wires the token from the environment and stops cleanly on Ctrl+C."""
    from pathlib import Path

    from leadgen import cli

    monkeypatch.chdir(Path(__file__).resolve().parent.parent)
    monkeypatch.setenv("LEADGEN_WEBHOOK_TOKEN", "from-env")
    seen = {}

    def fake_serve_forever(self, poll_interval=0.5):
        seen["token"] = self.token
        seen["url"] = self.url
        raise KeyboardInterrupt

    monkeypatch.setattr(server_mod.WebhookServer, "serve_forever", fake_serve_forever)
    envf = tmp_path / "e.env"
    envf.write_text("", encoding="utf-8")
    code = cli.main(["serve", "-p", "playbooks/demo-offline.yaml", "--db", str(tmp_path / "t.db"),
                     "--env-file", str(envf), "--port", "0"])
    out = capsys.readouterr().out
    assert code == 0 and seen["token"] == "from-env" and seen["url"].startswith("http://127.0.0.1:")
    assert "POST" in out and "/webhook/instantly" in out and "token required" in out and "Stopped." in out


def test_idle_client_does_not_block_other_requests(serve):
    """An idle open connection must not stall the single-threaded server."""
    import socket

    s = serve()
    host, port = s.httpd.server_address[:2]
    idle = socket.create_connection((host, port), timeout=5)
    try:
        idle.sendall(b"GET /health HTTP/1.1\r\nHost: x\r\n\r\n")
        first = idle.recv(4096)
        assert b"200 OK" in first and b"Connection: close" in first
        # the idle client never sends another request; others are still served
        assert s.request("GET", "/health")[0] == 200
    finally:
        idle.close()


def test_regression_query_token_is_not_logged(serve, caplog):
    s = serve()
    seed_lead(s.ctx)
    with caplog.at_level("DEBUG", logger=s.ctx.log.name):
        status, _, _ = s.request("POST", f"/webhook?token={TOKEN}", instantly_payload())
        s.request("POST", "/webhook?a=1&token=wrong-guess-XYZ", instantly_payload())
    assert status == 200
    assert "POST /webhook?token=***" in caplog.text     # the request is still logged ...
    assert TOKEN not in caplog.text and "wrong-guess-XYZ" not in caplog.text  # ... without the secret
