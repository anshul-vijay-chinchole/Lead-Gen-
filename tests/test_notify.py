"""Tests for the console / Slack / webhook notifiers and the notify dispatcher."""
from __future__ import annotations

import re
from datetime import date

import pytest

from leadgen import registry
from leadgen.context import MissingCredentialError
from leadgen.notify import notify
from leadgen.notify.console import ConsoleNotifier
from leadgen.notify.slack import SlackError, SlackNotifier, escape_mrkdwn
from leadgen.notify.webhook import WebhookError, WebhookNotifier, jsonable

SLACK_URL = "https://hooks.slack.com/services/T0001/B0002/SeCrEtToKeN123"
HOOK_URL = "https://hooks.example.com/leadgen/abc123secret"


# --- registry ---------------------------------------------------------------------

def test_registry_resolves_notifiers():
    assert registry.resolve("notifier", "console") is ConsoleNotifier
    assert registry.resolve("notifier", "slack") is SlackNotifier
    assert registry.resolve("notifier", "webhook") is WebhookNotifier
    assert ConsoleNotifier.offline is True
    assert SlackNotifier.offline is False and WebhookNotifier.offline is False
    assert SlackNotifier.env_key == "SLACK_WEBHOOK_URL"
    assert WebhookNotifier.env_key == "LEADGEN_WEBHOOK_URL"


# --- console -------------------------------------------------------------------------

def test_console_writes_block_to_stdout(make_ctx, capsys):
    ctx = make_ctx()
    ConsoleNotifier({"type": "console"}, ctx).send(
        "positive", "Positive reply: Jane (Acme)", "Line one\nLine two", {"x": 1})
    out = capsys.readouterr().out
    assert "[POSITIVE] Positive reply: Jane (Acme)" in out
    assert "Line one\nLine two" in out
    assert out.startswith("=" * 72)
    assert '"x"' not in out  # data hidden by default


def test_console_event_label_stderr_width_and_data(make_ctx, capsys):
    ctx = make_ctx()
    n = ConsoleNotifier({"stream": "stderr", "width": 30, "show_data": True}, ctx)
    n.send("run_summary", "Run finished", "", {"when": date(2026, 9, 24), "n": 3})
    cap = capsys.readouterr()
    assert cap.out == ""
    assert "[RUN SUMMARY] Run finished" in cap.err
    assert "=" * 30 + "\n" in cap.err and "=" * 31 not in cap.err
    assert '"when": "2026-09-24"' in cap.err  # dates survive JSON dump


def test_console_bad_width_and_empty_title(make_ctx, capsys):
    ctx = make_ctx()
    ConsoleNotifier({"width": "wide"}, ctx).send("", "", "body only", {})
    out = capsys.readouterr().out
    assert "(no title)" in out and "body only" in out


def test_console_events_filter(make_ctx, capsys):
    ctx = make_ctx()
    n = ConsoleNotifier({"events": ["positive"]}, ctx)
    n.send("run_summary", "ignored", "x", {})
    assert capsys.readouterr().out == ""
    n.send("positive", "shown", "x", {})
    assert "shown" in capsys.readouterr().out


def test_console_runs_in_dry_run(make_ctx, capsys):
    ctx = make_ctx(dry_run=True)
    ConsoleNotifier({}, ctx).send("positive", "Hello", "World", {})
    assert "Hello" in capsys.readouterr().out


# --- slack ----------------------------------------------------------------------------

def test_slack_posts_text_to_config_url(make_ctx):
    ctx = make_ctx()
    ctx.http.add("POST", SLACK_URL, text="ok")
    SlackNotifier({"type": "slack", "webhook_url": SLACK_URL}, ctx).send(
        "positive", "Positive reply", "Jane said yes", {"lead_id": "abc"})
    assert len(ctx.http.calls) == 1
    call = ctx.http.calls[0]
    assert call["method"] == "POST" and call["url"] == SLACK_URL
    assert call["json"] == {"text": "*Positive reply*\nJane said yes"}


def test_slack_url_from_env_and_custom_env(make_ctx):
    ctx = make_ctx(env={"SLACK_WEBHOOK_URL": SLACK_URL, "MY_SLACK": SLACK_URL + "X"})
    ctx.http.add("POST", SLACK_URL, text="ok")
    SlackNotifier({}, ctx).send("positive", "t", "b", {})
    SlackNotifier({"webhook_url_env": "MY_SLACK"}, ctx).send("positive", "t", "b", {})
    assert [c["url"] for c in ctx.http.calls] == [SLACK_URL, SLACK_URL + "X"]


def test_slack_missing_url_raises_lazily(make_ctx):
    ctx = make_ctx()
    n = SlackNotifier({"type": "slack"}, ctx)  # construction never needs the secret
    with pytest.raises(MissingCredentialError, match="SLACK_WEBHOOK_URL"):
        n.send("positive", "t", "b", {})
    assert ctx.http.calls == []


def test_slack_rejects_non_url(make_ctx):
    ctx = make_ctx()
    with pytest.raises(SlackError, match="http"):
        SlackNotifier({"webhook_url": "not-a-url"}, ctx).send("positive", "t", "b", {})
    assert ctx.http.calls == []


def test_slack_escapes_control_characters(make_ctx):
    ctx = make_ctx()
    ctx.http.add("POST", SLACK_URL, text="ok")
    SlackNotifier({"webhook_url": SLACK_URL}, ctx).send(
        "positive", "Jane <jane@acme.com>", "R&D said <yes>", {})
    text = ctx.http.calls[0]["json"]["text"]
    assert text == "*Jane &lt;jane@acme.com&gt;*\nR&amp;D said &lt;yes&gt;"
    assert escape_mrkdwn("a<b>&c") == "a&lt;b&gt;&amp;c"


def test_slack_escape_can_be_disabled(make_ctx):
    ctx = make_ctx()
    ctx.http.add("POST", SLACK_URL, text="ok")
    SlackNotifier({"webhook_url": SLACK_URL, "escape": False}, ctx).send(
        "positive", "t", "<https://x.com|link>", {})
    assert ctx.http.calls[0]["json"]["text"] == "*t*\n<https://x.com|link>"


def test_slack_truncates_long_text(make_ctx):
    ctx = make_ctx()
    ctx.http.add("POST", SLACK_URL, text="ok")
    SlackNotifier({"webhook_url": SLACK_URL}, ctx).send("run_summary", "Run", "x" * 10000, {})
    text = ctx.http.calls[0]["json"]["text"]
    assert len(text) <= 3000
    assert text.startswith("*Run*\nxxx") and text.endswith("…(truncated)")


def test_slack_truncation_never_leaves_half_entity(make_ctx):
    ctx = make_ctx()
    ctx.http.add("POST", SLACK_URL, text="ok")
    SlackNotifier({"webhook_url": SLACK_URL, "max_chars": 200}, ctx).send(
        "x", "T", "&" * 500, {})
    text = ctx.http.calls[0]["json"]["text"]
    assert len(text) <= 200
    body = text[len("*T*\n"):-len("\n…(truncated)")]
    assert re.fullmatch(r"(?:&amp;)+", body)


def test_slack_optional_overrides_only_when_set(make_ctx):
    ctx = make_ctx()
    ctx.http.add("POST", SLACK_URL, text="ok")
    SlackNotifier({"webhook_url": SLACK_URL, "channel": "#leads", "username": "leadgen",
                   "icon_emoji": ":dart:"}, ctx).send("positive", "t", "b", {})
    body = ctx.http.calls[0]["json"]
    assert body == {"text": "*t*\nb", "channel": "#leads", "username": "leadgen", "icon_emoji": ":dart:"}


def test_slack_http_error_hides_secret(make_ctx):
    ctx = make_ctx()
    ctx.http.add("POST", SLACK_URL, status=403, text="invalid_token")
    with pytest.raises(SlackError) as ei:
        SlackNotifier({"webhook_url": SLACK_URL}, ctx).send("positive", "t", "b", {})
    msg = str(ei.value)
    assert "403" in msg and "invalid_token" in msg
    assert "SeCrEtToKeN123" not in msg and "T0001" not in msg


def test_slack_network_error_hides_secret(make_ctx):
    from leadgen.http import HttpError

    ctx = make_ctx()

    def boom(call):
        raise HttpError(0, call["url"], "ConnectionError: refused")

    ctx.http.add("POST", SLACK_URL, fn=boom)
    with pytest.raises(SlackError) as ei:
        SlackNotifier({"webhook_url": SLACK_URL}, ctx).send("positive", "t", "b", {})
    assert "SeCrEtToKeN123" not in str(ei.value)
    assert "hooks.slack.com" in str(ei.value)


def test_slack_dry_run_is_a_noop(make_ctx):
    ctx = make_ctx(dry_run=True)  # no URL configured either: must not raise
    SlackNotifier({}, ctx).send("positive", "t", "b", {})
    assert ctx.http.calls == []


def test_slack_events_filter(make_ctx):
    ctx = make_ctx()
    ctx.http.add("POST", SLACK_URL, text="ok")
    n = SlackNotifier({"webhook_url": SLACK_URL, "events": ["positive", "referral"]}, ctx)
    n.send("run_summary", "t", "b", {})
    assert ctx.http.calls == []
    n.send("referral", "t", "b", {})
    assert len(ctx.http.calls) == 1


# --- webhook ---------------------------------------------------------------------------

def test_webhook_posts_event_json(make_ctx):
    ctx = make_ctx()
    ctx.http.add("POST", HOOK_URL, json={"received": True})
    WebhookNotifier({"type": "webhook", "url": HOOK_URL}, ctx).send(
        "positive", "Positive reply", "Jane said yes",
        {"lead_id": "abc", "when": date(2026, 9, 24), "tags": {"hot"}})
    call = ctx.http.calls[0]
    assert call["method"] == "POST" and call["url"] == HOOK_URL
    assert call["json"] == {"event": "positive", "title": "Positive reply", "text": "Jane said yes",
                            "data": {"lead_id": "abc", "when": "2026-09-24", "tags": ["hot"]}}
    assert call["headers"]["Content-Type"] == "application/json"


def test_webhook_custom_headers_method_and_env(make_ctx):
    ctx = make_ctx(env={"LEADGEN_WEBHOOK_URL": HOOK_URL})
    ctx.http.add("PUT", HOOK_URL, status=204)
    WebhookNotifier({"headers": {"Authorization": "Bearer t0k", "X-Source": "leadgen"},
                     "method": "put"}, ctx).send("referral", "t", "b", {})
    call = ctx.http.calls[0]
    assert call["method"] == "PUT"
    assert call["headers"]["Authorization"] == "Bearer t0k"
    assert call["headers"]["X-Source"] == "leadgen"


def test_webhook_respects_custom_content_type_and_include_data(make_ctx):
    ctx = make_ctx()
    ctx.http.add("POST", HOOK_URL, json={})
    WebhookNotifier({"url": HOOK_URL, "headers": {"content-type": "application/vnd+json"},
                     "include_data": False}, ctx).send("positive", "t", "b", {"secret": "x"})
    call = ctx.http.calls[0]
    assert call["headers"] == {"content-type": "application/vnd+json"}
    assert call["json"]["data"] == {}


def test_webhook_missing_url_raises_lazily(make_ctx):
    ctx = make_ctx()
    n = WebhookNotifier({}, ctx)
    with pytest.raises(MissingCredentialError, match="LEADGEN_WEBHOOK_URL"):
        n.send("positive", "t", "b", {})


def test_webhook_bad_config(make_ctx):
    ctx = make_ctx()
    with pytest.raises(WebhookError, match="method"):
        WebhookNotifier({"url": HOOK_URL, "method": "DELETE"}, ctx).send("e", "t", "b", {})
    with pytest.raises(WebhookError, match="headers"):
        WebhookNotifier({"url": HOOK_URL, "headers": ["x"]}, ctx).send("e", "t", "b", {})
    with pytest.raises(WebhookError, match="http"):
        WebhookNotifier({"url": "ftp://x"}, ctx).send("e", "t", "b", {})
    assert ctx.http.calls == []


def test_webhook_http_error_hides_secret(make_ctx):
    ctx = make_ctx()
    ctx.http.add("POST", HOOK_URL, status=500, json={"error": "boom"})
    with pytest.raises(WebhookError) as ei:
        WebhookNotifier({"url": HOOK_URL}, ctx).send("positive", "t", "b", {})
    assert "500" in str(ei.value) and "boom" in str(ei.value)
    assert "abc123secret" not in str(ei.value)


def test_webhook_dry_run_is_a_noop(make_ctx):
    ctx = make_ctx(dry_run=True)
    WebhookNotifier({"url": HOOK_URL}, ctx).send("positive", "t", "b", {})
    assert ctx.http.calls == []


def test_jsonable_handles_objects():
    from leadgen.models import Reply

    out = jsonable({"r": Reply(from_email="A@B.com", body="hi"), "t": (1, 2), "o": object()})
    assert out["r"]["from_email"] == "a@b.com" and out["t"] == [1, 2]
    assert isinstance(out["o"], str)


# --- dispatcher --------------------------------------------------------------------------

def test_notify_dispatches_to_every_channel(make_ctx, capsys):
    ctx = make_ctx(notify={"channels": [{"type": "console"},
                                        {"type": "slack", "webhook_url": SLACK_URL},
                                        {"type": "webhook", "url": HOOK_URL}],
                           "on": ["positive"]})
    ctx.http.add("POST", SLACK_URL, text="ok").add("POST", HOOK_URL, json={})
    assert notify(ctx, "positive", "Positive reply", "yes!", {"a": 1}) == 3
    assert "Positive reply" in capsys.readouterr().out
    assert len(ctx.http.calls) == 2
    # events not listed in notify.on are dropped entirely
    assert notify(ctx, "question", "Q", "?", {}) == 0


def test_notify_broken_channel_does_not_raise(make_ctx, capsys):
    ctx = make_ctx(notify={"channels": [{"type": "slack"}, {"type": "console"}], "on": ["positive"]})
    assert notify(ctx, "positive", "Title", "text") == 1  # slack has no URL; console still works
    assert "Title" in capsys.readouterr().out
