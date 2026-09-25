"""Tests for the LLM clients (OpenAI / OpenAI-compatible / Anthropic)."""
from __future__ import annotations

import pytest

from leadgen import registry
from leadgen.context import MissingCredentialError
from leadgen.http import HttpError
from leadgen.llm import LLMError, build_llm
from leadgen.llm.anthropic import AnthropicClient, messages_url
from leadgen.llm.openai import LLMConfigError, LLMTruncatedError, OpenAIClient
from tests.fakes import FakeHttp

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


# --- canned payloads (documented response shapes) ----------------------------------------

def openai_payload(content="Hello there", finish_reason="stop", refusal=None, **over):
    body = {
        "id": "chatcmpl-B9MHDbslfkBeAs8l4bebGdFOJ6PeG",
        "object": "chat.completion",
        "created": 1758700000,
        "model": "gpt-5-mini-2025-08-07",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content, "refusal": refusal, "annotations": []},
            "logprobs": None,
            "finish_reason": finish_reason,
        }],
        "usage": {"prompt_tokens": 812, "completion_tokens": 403, "total_tokens": 1215,
                  "completion_tokens_details": {"reasoning_tokens": 192}},
        "service_tier": "default",
        "system_fingerprint": None,
    }
    body.update(over)
    return body


def anthropic_payload(text="Hello there", stop_reason="end_turn", blocks=None, **over):
    body = {
        "id": "msg_01XFDUDYJgAACzvnptvVoYEL",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5",
        "content": blocks if blocks is not None else [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "stop_details": None,
        "usage": {"input_tokens": 812, "output_tokens": 403, "cache_creation_input_tokens": 0,
                  "cache_read_input_tokens": 0},
    }
    body.update(over)
    return body


class RecordingHttp(FakeHttp):
    """FakeHttp that also records the timeout passed to each request."""

    def __init__(self) -> None:
        super().__init__()
        self.timeouts = []

    def request(self, method, url, **kw):
        self.timeouts.append(kw.get("timeout"))
        return super().request(method, url, **kw)


def openai_client(make_ctx, config=None, env=None):
    ctx = make_ctx(env={"OPENAI_API_KEY": "sk-test"} if env is None else env)
    cfg = {"type": "openai"}
    cfg.update(config or {})
    return OpenAIClient(cfg, ctx), ctx


def anthropic_client(make_ctx, config=None, env=None):
    ctx = make_ctx(env={"ANTHROPIC_API_KEY": "sk-ant-test"} if env is None else env)
    cfg = {"type": "anthropic"}
    cfg.update(config or {})
    return AnthropicClient(cfg, ctx), ctx


# --- registry / build_llm -------------------------------------------------------------------

def test_registry_resolves_llm_clients():
    assert registry.resolve("llm", "openai") is OpenAIClient
    assert registry.resolve("llm", "openai_compatible") is OpenAIClient
    assert registry.resolve("llm", "anthropic") is AnthropicClient


def test_build_llm_from_playbook_writer_section(make_ctx):
    ctx = make_ctx(env={"ANTHROPIC_API_KEY": "k"},
                   writer={"type": "ai", "provider": "anthropic", "model": "claude-opus-5"})
    llm = ctx.llm
    assert isinstance(llm, AnthropicClient) and llm.model == "claude-opus-5"

    ctx2 = make_ctx(env={"OPENROUTER_API_KEY": "or-key"},
                    writer={"type": "ai", "provider": "openai_compatible", "model": "meta/llama-4",
                            "base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY"})
    llm2 = build_llm(ctx2)
    assert isinstance(llm2, OpenAIClient) and llm2.compatible
    assert llm2.api_key() == "or-key"


def test_constructors_never_touch_credentials(make_ctx):
    ctx = make_ctx(env={})
    OpenAIClient({"type": "openai"}, ctx)
    OpenAIClient({"type": "openai_compatible"}, ctx)
    AnthropicClient({"type": "anthropic"}, ctx)
    assert ctx.http.calls == []


# --- OpenAI -----------------------------------------------------------------------------------

def test_openai_request_shape_and_text(make_ctx):
    client, ctx = openai_client(make_ctx)
    ctx.http = RecordingHttp()
    ctx.http.add("POST", OPENAI_URL, json=openai_payload("Hi Jane"))
    out = client.complete("You are a copywriter.", "Write one line.")
    assert out == "Hi Jane"
    call = ctx.http.calls[0]
    assert call["url"] == OPENAI_URL and call["method"] == "POST"
    assert call["headers"]["Authorization"] == "Bearer sk-test"
    body = call["json"]
    assert body["model"] == "gpt-5-mini"
    assert body["messages"] == [{"role": "system", "content": "You are a copywriter."},
                                {"role": "user", "content": "Write one line."}]
    assert body["max_completion_tokens"] == 1500 and "max_tokens" not in body
    assert "temperature" not in body and "response_format" not in body
    assert ctx.http.timeouts == [120.0]


def test_openai_json_mode_and_complete_json(make_ctx):
    client, ctx = openai_client(make_ctx, {"model": "gpt-5", "temperature": 0.4})
    ctx.http.add("POST", OPENAI_URL, json=openai_payload('{"emails": [{"step": 1}]}'))
    data = client.complete_json("Return JSON with emails.", "go", max_tokens=3000)
    assert data == {"emails": [{"step": 1}]}
    body = ctx.http.calls[0]["json"]
    assert body["response_format"] == {"type": "json_object"}
    assert body["model"] == "gpt-5" and body["max_completion_tokens"] == 3000
    assert body["temperature"] == 0.4  # config default used when the call passes None


def test_openai_json_mode_adds_json_word_when_missing(make_ctx):
    client, ctx = openai_client(make_ctx)
    ctx.http.add("POST", OPENAI_URL, json=openai_payload('{"a": 1}'))
    client.complete("Be brief.", "Say hi", json_mode=True)
    system = ctx.http.calls[0]["json"]["messages"][0]["content"]
    assert system.startswith("Be brief.") and "JSON" in system


def test_openai_explicit_temperature_overrides_config(make_ctx):
    client, ctx = openai_client(make_ctx, {"temperature": 0.9})
    ctx.http.add("POST", OPENAI_URL, json=openai_payload())
    client.complete("s", "u", temperature=0.0)
    assert ctx.http.calls[0]["json"]["temperature"] == 0.0


def test_openai_optional_config_passthrough(make_ctx):
    client, ctx = openai_client(make_ctx, {"reasoning_effort": "low", "extra_body": {"seed": 7},
                                           "extra_headers": {"X-Title": "leadgen"}, "timeout": 30})
    ctx.http = RecordingHttp()
    ctx.http.add("POST", OPENAI_URL, json=openai_payload())
    client.complete("s", "u")
    call = ctx.http.calls[0]
    assert call["json"]["reasoning_effort"] == "low" and call["json"]["seed"] == 7
    assert call["headers"]["X-Title"] == "leadgen"
    assert ctx.http.timeouts == [30.0]


def test_openai_content_parts_are_joined(make_ctx):
    client, ctx = openai_client(make_ctx)
    ctx.http.add("POST", OPENAI_URL, json=openai_payload([{"type": "text", "text": "Hel"},
                                                          {"type": "text", "text": "lo"}]))
    assert client.complete("s", "u") == "Hello"


@pytest.mark.parametrize("payload, match", [
    ({"id": "x", "object": "chat.completion", "choices": []}, "no choices"),
    ({"error": {"message": "The model `gpt-9` does not exist", "type": "invalid_request_error"}},
     "does not exist"),
    (["a", "list"], "unexpected response"),
])
def test_openai_missing_choices(make_ctx, payload, match):
    client, ctx = openai_client(make_ctx)
    ctx.http.add("POST", OPENAI_URL, json=payload)
    with pytest.raises(LLMError, match=match):
        client.complete("s", "u")


def test_openai_refusal_empty_and_filter(make_ctx):
    client, ctx = openai_client(make_ctx)
    ctx.http.add("POST", OPENAI_URL, json=openai_payload(None, refusal="I can't help with that."), times=1)
    ctx.http.add("POST", OPENAI_URL, json=openai_payload("   "), times=1)
    ctx.http.add("POST", OPENAI_URL, json=openai_payload("partial", finish_reason="content_filter"), times=1)
    with pytest.raises(LLMError, match="refused"):
        client.complete("s", "u")
    with pytest.raises(LLMError, match="empty model output"):
        client.complete("s", "u")
    with pytest.raises(LLMError, match="content filter"):
        client.complete("s", "u")


def test_openai_truncated_output(make_ctx):
    client, ctx = openai_client(make_ctx)
    # reasoning models can burn the whole budget on hidden reasoning: empty + length
    ctx.http.add("POST", OPENAI_URL, json=openai_payload("", finish_reason="length"))
    with pytest.raises(LLMTruncatedError, match="truncated") as ei:
        client.complete("s", "u")
    assert getattr(ei.value, "truncated", False) and isinstance(ei.value, LLMError)


def test_openai_non_json_body(make_ctx):
    client, ctx = openai_client(make_ctx)
    ctx.http.add("POST", OPENAI_URL, text="<html>Bad gateway</html>")
    with pytest.raises(LLMError, match="not JSON"):
        client.complete("s", "u")


def test_openai_http_error_propagates(make_ctx):
    client, ctx = openai_client(make_ctx)
    ctx.http.add("POST", OPENAI_URL, status=401,
                 json={"error": {"message": "Incorrect API key provided", "type": "invalid_request_error",
                                 "code": "invalid_api_key"}})
    with pytest.raises(HttpError) as ei:
        client.complete("s", "u")
    assert ei.value.status == 401


def test_openai_retries_without_rejected_temperature(make_ctx):
    client, ctx = openai_client(make_ctx, {"temperature": 0.7})
    ctx.http.add("POST", OPENAI_URL, status=400, times=1, json={"error": {
        "message": "Unsupported value: 'temperature' does not support 0.7 with this model. "
                   "Only the default (1) value is supported.",
        "type": "invalid_request_error", "param": "temperature", "code": "unsupported_value"}})
    ctx.http.add("POST", OPENAI_URL, json=openai_payload("ok"))
    assert client.complete("s", "u") == "ok"
    assert "temperature" in ctx.http.calls[0]["json"]
    assert "temperature" not in ctx.http.calls[1]["json"]
    client.complete("s", "u")  # remembered: not sent again
    assert "temperature" not in ctx.http.calls[2]["json"] and len(ctx.http.calls) == 3


def test_openai_other_400_is_not_retried(make_ctx):
    client, ctx = openai_client(make_ctx, {"temperature": 0.7})
    ctx.http.add("POST", OPENAI_URL, status=400, json={"error": {"message": "context length exceeded"}})
    with pytest.raises(HttpError):
        client.complete("s", "u")
    assert len(ctx.http.calls) == 1


def test_openai_missing_credential_is_lazy(make_ctx):
    client, ctx = openai_client(make_ctx, env={})
    with pytest.raises(MissingCredentialError, match="OPENAI_API_KEY"):
        client.complete("s", "u")
    assert ctx.http.calls == []


def test_openai_api_key_env_override(make_ctx):
    client, ctx = openai_client(make_ctx, {"api_key_env": "MY_KEY"}, env={"MY_KEY": "sk-mine"})
    ctx.http.add("POST", OPENAI_URL, json=openai_payload())
    client.complete("s", "u")
    assert ctx.http.calls[0]["headers"]["Authorization"] == "Bearer sk-mine"


# --- OpenAI-compatible -------------------------------------------------------------------------

def test_compatible_requires_base_url_and_model(make_ctx):
    client, ctx = openai_client(make_ctx, {"type": "openai_compatible"}, env={"OPENROUTER_API_KEY": "k"})
    with pytest.raises(LLMConfigError, match="base_url and model required") as ei:
        client.complete("s", "u")
    assert getattr(ei.value, "permanent", False)
    client2, _ = openai_client(make_ctx, {"type": "openai_compatible", "base_url": "https://x.ai/v1"})
    with pytest.raises(LLMConfigError, match="model required"):
        client2.complete("s", "u")
    assert ctx.http.calls == []


def test_compatible_request_shape(make_ctx):
    client, ctx = openai_client(make_ctx, {
        "type": "openai_compatible", "base_url": "https://openrouter.ai/api/v1/",
        "model": "anthropic/claude-sonnet-5", "api_key_env": "OPENROUTER_API_KEY"},
        env={"OPENROUTER_API_KEY": "or-123", "OPENAI_API_KEY": "sk-openai"})
    url = "https://openrouter.ai/api/v1/chat/completions"
    ctx.http.add("POST", url, json=openai_payload('{"ok": true}'))
    assert client.complete_json("Reply in JSON.", "u", max_tokens=900) == {"ok": True}
    call = ctx.http.calls[0]
    assert call["url"] == url
    assert call["headers"]["Authorization"] == "Bearer or-123"
    body = call["json"]
    assert body["max_tokens"] == 900 and "max_completion_tokens" not in body
    assert body["model"] == "anthropic/claude-sonnet-5"
    assert body["response_format"] == {"type": "json_object"}


def test_compatible_never_sends_openai_key_to_third_party(make_ctx):
    client, ctx = openai_client(make_ctx, {"type": "openai_compatible", "base_url": "https://api.groq.com/openai/v1",
                                           "model": "llama-4"}, env={"OPENAI_API_KEY": "sk-openai"})
    with pytest.raises(MissingCredentialError, match="api.groq.com"):
        client.complete("s", "u")
    assert ctx.http.calls == []


def test_compatible_local_server_without_auth(make_ctx):
    client, ctx = openai_client(make_ctx, {"type": "openai_compatible", "base_url": "http://localhost:11434/v1",
                                           "model": "llama3.1", "api_key_required": False,
                                           "json_response_format": False, "max_tokens_param": "num_predict"},
                                env={})
    url = "http://localhost:11434/v1/chat/completions"
    ctx.http.add("POST", url, json=openai_payload("hi"))
    assert client.complete("s", "u", json_mode=True) == "hi"
    call = ctx.http.calls[0]
    assert "Authorization" not in call["headers"]
    assert "response_format" not in call["json"] and call["json"]["num_predict"] == 1500


def test_openai_bad_base_url(make_ctx):
    client, _ = openai_client(make_ctx, {"base_url": "api.openai.com/v1"})
    with pytest.raises(LLMConfigError, match="http"):
        client.complete("s", "u")


# --- Anthropic ----------------------------------------------------------------------------------

def test_anthropic_request_shape_and_text(make_ctx):
    client, ctx = anthropic_client(make_ctx)
    ctx.http = RecordingHttp()
    ctx.http.add("POST", ANTHROPIC_URL, json=anthropic_payload("Hi Jane"))
    assert client.complete("You are a copywriter.", "Write one line.", max_tokens=800) == "Hi Jane"
    call = ctx.http.calls[0]
    assert call["url"] == ANTHROPIC_URL
    h = call["headers"]
    assert h["x-api-key"] == "sk-ant-test" and h["anthropic-version"] == "2023-06-01"
    assert h["content-type"] == "application/json" and "Authorization" not in h
    body = call["json"]
    assert body == {"model": "claude-opus-5", "max_tokens": 800, "system": "You are a copywriter.",
                    "messages": [{"role": "user", "content": "Write one line."}]}
    assert ctx.http.timeouts == [120.0]


def test_anthropic_json_mode_uses_system_instruction_not_prefill(make_ctx):
    client, ctx = anthropic_client(make_ctx, {"temperature": 0.3, "model": "claude-haiku-4-5"})
    ctx.http.add("POST", ANTHROPIC_URL, json=anthropic_payload('```json\n{"a": [1, 2]}\n```'))
    assert client.complete_json("Write copy.", "go") == {"a": [1, 2]}
    body = ctx.http.calls[0]["json"]
    assert body["system"].startswith("Write copy.")
    assert "single JSON object and nothing else" in body["system"]
    assert body["messages"] == [{"role": "user", "content": "go"}]  # no assistant prefill
    assert body["temperature"] == 0.3 and body["model"] == "claude-haiku-4-5"


def test_anthropic_joins_text_blocks_and_skips_thinking(make_ctx):
    client, ctx = anthropic_client(make_ctx)
    blocks = [{"type": "thinking", "thinking": "", "signature": "EqQBCgIYAh..."},
              {"type": "text", "text": '{"emails": '},
              {"type": "text", "text": "[]}"}]
    ctx.http.add("POST", ANTHROPIC_URL, json=anthropic_payload(blocks=blocks))
    assert client.complete("s", "u") == '{"emails": []}'


def test_anthropic_truncated_refusal_empty(make_ctx):
    client, ctx = anthropic_client(make_ctx)
    ctx.http.add("POST", ANTHROPIC_URL, json=anthropic_payload('{"emails": [', stop_reason="max_tokens"), times=1)
    ctx.http.add("POST", ANTHROPIC_URL, times=1, json=anthropic_payload(
        blocks=[], stop_reason="refusal",
        stop_details={"type": "refusal", "category": "cyber", "explanation": "declined"}))
    ctx.http.add("POST", ANTHROPIC_URL, json=anthropic_payload(blocks=[{"type": "thinking", "thinking": ""}]),
                 times=1)
    with pytest.raises(LLMTruncatedError, match="output truncated"):
        client.complete("s", "u")
    with pytest.raises(LLMError, match="refused.*cyber"):
        client.complete("s", "u")
    with pytest.raises(LLMError, match="empty model output"):
        client.complete("s", "u")


def test_anthropic_malformed_payloads(make_ctx):
    client, ctx = anthropic_client(make_ctx)
    ctx.http.add("POST", ANTHROPIC_URL, times=1, json={"type": "error", "error": {
        "type": "overloaded_error", "message": "Overloaded"}})
    ctx.http.add("POST", ANTHROPIC_URL, times=1, json={"id": "msg_1", "type": "message"})
    ctx.http.add("POST", ANTHROPIC_URL, times=1, json=["not", "a", "dict"])
    with pytest.raises(LLMError, match="Overloaded"):
        client.complete("s", "u")
    with pytest.raises(LLMError, match="empty model output"):
        client.complete("s", "u")
    with pytest.raises(LLMError, match="unexpected response"):
        client.complete("s", "u")


def test_anthropic_http_error_and_missing_key(make_ctx):
    client, ctx = anthropic_client(make_ctx)
    ctx.http.add("POST", ANTHROPIC_URL, status=404, json={"type": "error", "error": {
        "type": "not_found_error", "message": "model: claude-nope"}})
    with pytest.raises(HttpError) as ei:
        client.complete("s", "u")
    assert ei.value.status == 404
    nokey, ctx2 = anthropic_client(make_ctx, env={})
    with pytest.raises(MissingCredentialError, match="ANTHROPIC_API_KEY"):
        nokey.complete("s", "u")
    assert ctx2.http.calls == []


def test_anthropic_retries_without_rejected_temperature(make_ctx):
    client, ctx = anthropic_client(make_ctx, {"temperature": 0.5})
    ctx.http.add("POST", ANTHROPIC_URL, status=400, times=1, json={"type": "error", "error": {
        "type": "invalid_request_error", "message": "temperature is not supported for this model"}})
    ctx.http.add("POST", ANTHROPIC_URL, json=anthropic_payload("ok"))
    assert client.complete("s", "u") == "ok"
    assert "temperature" in ctx.http.calls[0]["json"] and "temperature" not in ctx.http.calls[1]["json"]


def test_anthropic_optional_config(make_ctx):
    client, ctx = anthropic_client(make_ctx, {"effort": "low", "extra_headers": {"anthropic-beta": "x-2026"},
                                              "extra_body": {"metadata": {"user_id": "run-1"}},
                                              "base_url": "https://gateway.example.com/anthropic"})
    url = "https://gateway.example.com/anthropic/v1/messages"
    ctx.http.add("POST", url, json=anthropic_payload("ok"))
    client.complete("", "u")
    call = ctx.http.calls[0]
    assert call["url"] == url and call["headers"]["anthropic-beta"] == "x-2026"
    assert call["json"]["output_config"] == {"effort": "low"}
    assert call["json"]["metadata"] == {"user_id": "run-1"}
    assert "system" not in call["json"]  # empty system prompt omitted


@pytest.mark.parametrize("base, expected", [
    ("", ANTHROPIC_URL),
    ("https://api.anthropic.com", ANTHROPIC_URL),
    ("https://api.anthropic.com/", ANTHROPIC_URL),
    ("https://api.anthropic.com/v1", ANTHROPIC_URL),
    ("https://proxy.local/v1/messages", "https://proxy.local/v1/messages"),
])
def test_anthropic_messages_url(base, expected):
    assert messages_url(base) == expected


# --- regressions: credential hygiene -------------------------------------------------------------

@pytest.mark.parametrize("raw", ["sk-secret-123\n", " sk-secret-123", "sk-secret-123\r\n", "\tsk-secret-123 "])
def test_api_keys_are_stripped_before_use(make_ctx, raw):
    # mounted secrets often end in a newline; requests would reject the header and echo the key
    oa, _ = openai_client(make_ctx, env={"OPENAI_API_KEY": raw})
    assert oa.build_request("s", "u")[1]["Authorization"] == "Bearer sk-secret-123"
    an, _ = anthropic_client(make_ctx, env={"ANTHROPIC_API_KEY": raw})
    assert an.build_request("s", "u")[1]["x-api-key"] == "sk-secret-123"


@pytest.mark.parametrize("raw", ["sk-secret\n123", "sk-secret 123", "sk-secret\x00123", "sk-sécret123"])
def test_malformed_api_keys_are_refused_without_echoing_them(make_ctx, raw):
    for client, ctx in (openai_client(make_ctx, env={"OPENAI_API_KEY": raw}),
                        anthropic_client(make_ctx, env={"ANTHROPIC_API_KEY": raw})):
        with pytest.raises(MissingCredentialError, match="whitespace, control or non-ASCII") as ei:
            client.complete("s", "u")
        assert "secret" not in str(ei.value) and "123" not in str(ei.value)
        assert ctx.http.calls == []


def test_openai_type_with_custom_base_url_never_sends_openai_key(make_ctx):
    client, ctx = openai_client(make_ctx, {"base_url": "https://openrouter.ai/api/v1", "model": "x"},
                                env={"OPENAI_API_KEY": "sk-openai-secret"})
    with pytest.raises(MissingCredentialError, match="api_key_env") as ei:
        client.complete("s", "u")
    assert "sk-openai-secret" not in str(ei.value) and ctx.http.calls == []
    # naming the key explicitly is a deliberate opt-in (e.g. an OpenAI proxy)
    client2, _ = openai_client(make_ctx, {"base_url": "https://oai.proxy.example/v1", "api_key_env": "OPENAI_API_KEY"},
                               env={"OPENAI_API_KEY": "sk-openai-secret"})
    assert client2.build_request("s", "u")[1]["Authorization"] == "Bearer sk-openai-secret"
    # the default / official host keeps working without extra config
    client3, _ = openai_client(make_ctx, {"base_url": "https://api.openai.com/v1/"})
    assert client3.build_request("s", "u")[1]["Authorization"] == "Bearer sk-test"


# --- token usage: last_usage + the run's usage meter ---------------------------------------------

def _llm_row(ctx, type_):
    rows = [r for r in ctx.usage.rows() if r["kind"] == "llm" and r["type"] == type_]
    return rows[0] if rows else None


def test_openai_usage_is_captured_and_metered(make_ctx):
    ctx = make_ctx(env={"OPENAI_API_KEY": "sk-test"})
    client = registry.create("llm", {"type": "openai", "model": "gpt-5-mini"}, ctx)
    assert client.last_usage is None
    ctx.http.add("POST", OPENAI_URL, json=openai_payload("Hi"))
    assert client.complete("s", "u") == "Hi"
    assert client.last_usage == {"input_tokens": 812, "output_tokens": 403}
    row = _llm_row(ctx, "openai")
    assert row["input_tokens"] == 812 and row["output_tokens"] == 403 and row["paid"]
    expected = ctx.usage.llm_cost("gpt-5-mini", 812, 403)
    assert row["estimated_cost_usd"] == pytest.approx(round(expected, 4))
    client.complete("s", "u")  # a second call adds up
    assert _llm_row(ctx, "openai")["input_tokens"] == 1624
    assert client.last_usage == {"input_tokens": 812, "output_tokens": 403}  # per call, not cumulative


def test_openai_usage_priced_with_configured_model_price(make_ctx):
    ctx = make_ctx(env={"OPENAI_API_KEY": "sk-test"},
                   usage={"llm_price_per_mtok": {"gpt-5-mini": {"input": 1.0, "output": 2.0}}})
    client = registry.create("llm", {"type": "openai", "model": "gpt-5-mini"}, ctx)
    ctx.http.add("POST", OPENAI_URL, json=openai_payload("Hi", usage={"prompt_tokens": 1_000_000,
                                                                       "completion_tokens": 500_000}))
    client.complete("s", "u")
    assert ctx.usage.estimated_cost == pytest.approx(2.0)


def test_openai_compatible_usage_recorded_under_its_type(make_ctx):
    ctx = make_ctx(env={"OPENROUTER_API_KEY": "or-key"})
    client = registry.create("llm", {"type": "openai_compatible", "model": "meta/llama-4",
                                     "base_url": "https://openrouter.ai/api/v1",
                                     "api_key_env": "OPENROUTER_API_KEY"}, ctx)
    # some compatible servers name the fields input_tokens / output_tokens
    ctx.http.add("POST", "https://openrouter.ai/api/v1/chat/completions",
                 json=openai_payload("ok", usage={"input_tokens": 50, "output_tokens": 7}))
    client.complete("s", "u")
    assert client.last_usage == {"input_tokens": 50, "output_tokens": 7}
    assert _llm_row(ctx, "openai_compatible")["output_tokens"] == 7
    assert _llm_row(ctx, "openai") is None


@pytest.mark.parametrize("usage", [None, {}, {"total_tokens": 12}, "n/a", {"prompt_tokens": None}])
def test_openai_missing_usage_leaves_last_usage_none(make_ctx, usage):
    ctx = make_ctx(env={"OPENAI_API_KEY": "sk-test"})
    client = registry.create("llm", {"type": "openai"}, ctx)
    payload = openai_payload("ok")
    if usage is None:
        del payload["usage"]
    else:
        payload["usage"] = usage
    ctx.http.add("POST", OPENAI_URL, json=payload)
    assert client.complete("s", "u") == "ok"
    assert client.last_usage is None
    row = _llm_row(ctx, "openai")
    assert row["calls"] == 1 and row["input_tokens"] == 0 and row["output_tokens"] == 0


def test_openai_partial_usage_counts_missing_side_as_zero(make_ctx):
    ctx = make_ctx(env={"OPENAI_API_KEY": "sk-test"})
    client = registry.create("llm", {"type": "openai"}, ctx)
    ctx.http.add("POST", OPENAI_URL, json=openai_payload("ok", usage={"prompt_tokens": "120"}))
    client.complete("s", "u")
    assert client.last_usage == {"input_tokens": 120, "output_tokens": 0}


def test_openai_failed_call_resets_last_usage(make_ctx):
    ctx = make_ctx(env={"OPENAI_API_KEY": "sk-test"})
    client = registry.create("llm", {"type": "openai"}, ctx)
    ctx.http.add("POST", OPENAI_URL, json=openai_payload("ok"), times=1)
    ctx.http.add("POST", OPENAI_URL, status=500, json={"error": {"message": "boom"}})
    client.complete("s", "u")
    assert client.last_usage is not None
    with pytest.raises(HttpError):
        client.complete("s", "u")
    assert client.last_usage is None  # never the previous call's numbers
    assert _llm_row(ctx, "openai")["input_tokens"] == 812  # only the successful call was metered


def test_openai_truncated_answer_is_still_metered(make_ctx):
    ctx = make_ctx(env={"OPENAI_API_KEY": "sk-test"})
    client = registry.create("llm", {"type": "openai"}, ctx)
    ctx.http.add("POST", OPENAI_URL, json=openai_payload("", finish_reason="length",
                                                         usage={"prompt_tokens": 90, "completion_tokens": 1500}))
    with pytest.raises(LLMTruncatedError):
        client.complete("s", "u", max_tokens=1500)
    assert client.last_usage == {"input_tokens": 90, "output_tokens": 1500}  # billed tokens
    assert _llm_row(ctx, "openai")["output_tokens"] == 1500


def test_openai_temperature_retry_meters_only_the_answer(make_ctx):
    ctx = make_ctx(env={"OPENAI_API_KEY": "sk-test"})
    client = registry.create("llm", {"type": "openai", "temperature": 0.7}, ctx)
    ctx.http.add("POST", OPENAI_URL, status=400, times=1,
                 json={"error": {"message": "Unsupported value: 'temperature'", "param": "temperature"}})
    ctx.http.add("POST", OPENAI_URL, json=openai_payload("ok"))
    client.complete("s", "u")
    assert _llm_row(ctx, "openai")["input_tokens"] == 812 and len(ctx.http.calls) == 2


def test_anthropic_usage_is_captured_and_metered(make_ctx):
    ctx = make_ctx(env={"ANTHROPIC_API_KEY": "sk-ant-test"})
    client = registry.create("llm", {"type": "anthropic", "model": "claude-sonnet-5"}, ctx)
    assert client.last_usage is None
    ctx.http.add("POST", ANTHROPIC_URL, json=anthropic_payload("Hi"))
    assert client.complete("s", "u") == "Hi"
    assert client.last_usage == {"input_tokens": 812, "output_tokens": 403}
    row = _llm_row(ctx, "anthropic")
    assert row["input_tokens"] == 812 and row["output_tokens"] == 403
    # claude-sonnet-5 list price: $2 in / $10 out per million tokens
    assert row["estimated_cost_usd"] == pytest.approx(round((812 * 2 + 403 * 10) / 1e6, 4))
    assert row["calls"] == 1 and ctx.usage.paid_lookups == 1  # the request itself is a paid lookup


def test_anthropic_cache_tokens_count_as_input(make_ctx):
    ctx = make_ctx(env={"ANTHROPIC_API_KEY": "k"})
    client = registry.create("llm", {"type": "anthropic"}, ctx)
    ctx.http.add("POST", ANTHROPIC_URL, json=anthropic_payload("ok", usage={
        "input_tokens": 10, "output_tokens": 5, "cache_creation_input_tokens": 100,
        "cache_read_input_tokens": 1000}))
    client.complete("s", "u")
    assert client.last_usage == {"input_tokens": 1110, "output_tokens": 5}


@pytest.mark.parametrize("usage", [None, {}, [], {"service_tier": "standard"}])
def test_anthropic_missing_usage_leaves_last_usage_none(make_ctx, usage):
    ctx = make_ctx(env={"ANTHROPIC_API_KEY": "k"})
    client = registry.create("llm", {"type": "anthropic"}, ctx)
    payload = anthropic_payload("ok")
    if usage is None:
        del payload["usage"]
    else:
        payload["usage"] = usage
    ctx.http.add("POST", ANTHROPIC_URL, json=payload)
    assert client.complete("s", "u") == "ok"
    assert client.last_usage is None
    row = _llm_row(ctx, "anthropic")
    assert row["calls"] == 1 and row["input_tokens"] == 0 and row["output_tokens"] == 0


def test_anthropic_truncated_and_failed_calls(make_ctx):
    ctx = make_ctx(env={"ANTHROPIC_API_KEY": "k"})
    client = registry.create("llm", {"type": "anthropic"}, ctx)
    ctx.http.add("POST", ANTHROPIC_URL, times=1, json=anthropic_payload(
        "cut", stop_reason="max_tokens", usage={"input_tokens": 40, "output_tokens": 300}))
    ctx.http.add("POST", ANTHROPIC_URL, status=529, json={"type": "error", "error": {"type": "overloaded_error"}})
    with pytest.raises(LLMTruncatedError):
        client.complete("s", "u", max_tokens=300)
    assert client.last_usage == {"input_tokens": 40, "output_tokens": 300}
    with pytest.raises(HttpError):
        client.complete("s", "u")
    assert client.last_usage is None
    assert _llm_row(ctx, "anthropic")["output_tokens"] == 300


def test_hand_built_clients_record_under_llm_and_their_name(make_ctx):
    client, ctx = anthropic_client(make_ctx)  # built without the registry: no kind / type set
    ctx.http.add("POST", ANTHROPIC_URL, json=anthropic_payload("ok"))
    client.complete("s", "u")
    assert _llm_row(ctx, "anthropic")["input_tokens"] == 812
    oa, ctx2 = openai_client(make_ctx)
    ctx2.http.add("POST", OPENAI_URL, json=openai_payload("ok"))
    oa.complete("s", "u")
    assert _llm_row(ctx2, "openai")["output_tokens"] == 403


def test_usage_capture_without_a_meter(make_ctx):
    client, ctx = anthropic_client(make_ctx)
    ctx.usage = None  # e.g. a bare context in a script
    ctx.http.add("POST", ANTHROPIC_URL, json=anthropic_payload("ok"))
    assert client.complete("s", "u") == "ok"
    assert client.last_usage == {"input_tokens": 812, "output_tokens": 403}
    oa, ctx2 = openai_client(make_ctx)
    ctx2.usage = None
    ctx2.http.add("POST", OPENAI_URL, json=openai_payload("ok"))
    oa.complete("s", "u")
    assert oa.last_usage == {"input_tokens": 812, "output_tokens": 403}


def test_last_usage_is_per_instance(make_ctx):
    a, ctx = anthropic_client(make_ctx)
    b = AnthropicClient({"type": "anthropic"}, ctx)
    ctx.http.add("POST", ANTHROPIC_URL, json=anthropic_payload("ok"))
    a.complete("s", "u")
    assert a.last_usage is not None and b.last_usage is None and AnthropicClient.last_usage is None
