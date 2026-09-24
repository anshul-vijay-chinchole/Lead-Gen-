"""OpenAI Chat Completions client (``type: openai``) - also serves any
OpenAI-compatible endpoint (``type: openai_compatible``: OpenRouter, Groq,
Together, Fireworks, DeepSeek, Mistral, a local vLLM / Ollama / LiteLLM ...).

Request::

    POST {base_url}/chat/completions
    Authorization: Bearer <key>
    {"model": "gpt-5-mini",
     "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": USER}],
     "response_format": {"type": "json_object"},      # json_mode only
     "max_completion_tokens": 1500,                   # "max_tokens" for openai_compatible
     "temperature": 0.7}                              # only when not None

Response (documented shape)::

    {"id": "chatcmpl-...", "object": "chat.completion", "model": "gpt-5-mini-...",
     "choices": [{"index": 0, "finish_reason": "stop",
                  "message": {"role": "assistant", "content": "...", "refusal": null}}],
     "usage": {"prompt_tokens": 812, "completion_tokens": 403, "total_tokens": 1215}}

``complete`` returns ``choices[0].message.content`` (a list of ``{"type": "text"}``
parts, as some compatible servers send, is joined). It raises ``LLMError`` when
the response has no choices, the model refused (``message.refusal`` or
``finish_reason == "content_filter"``) or the content is empty, and
``LLMTruncatedError`` when ``finish_reason == "length"`` (reasoning models such as
gpt-5-mini count their hidden reasoning tokens against the budget, so a small
``max_tokens`` can come back empty *and* truncated). HTTP failures surface as
``leadgen.http.HttpError`` (the shared client already retried 429/5xx).

Credentials
-----------
``type: openai`` reads ``OPENAI_API_KEY`` (config ``api_key`` / ``api_key_env``
override, as for every adapter). ``type: openai_compatible`` needs an explicit
``api_key_env`` (e.g. ``OPENROUTER_API_KEY``) or ``api_key``: the OpenAI key is
only ever sent to ``api.openai.com``, never silently to a third-party host. A
local server without auth can set ``api_key_required: false``. Keys are resolved
lazily at the first request, so constructing the client never fails.

Config keys
-----------
type                   ``openai`` or ``openai_compatible`` (set by the registry).
model                  Model id. Default ``gpt-5-mini`` for ``openai``;
                       REQUIRED for ``openai_compatible``.
base_url               API root, default ``https://api.openai.com/v1``;
                       REQUIRED for ``openai_compatible`` (e.g.
                       ``https://openrouter.ai/api/v1``). Trailing ``/`` ignored.
path                   Endpoint path appended to ``base_url`` (default
                       ``/chat/completions``).
temperature            Default sampling temperature used when ``complete`` is
                       called with ``temperature=None``. Only sent when not
                       None. Many current models (gpt-5 family, reasoning
                       models) reject a custom temperature: on an HTTP 400 that
                       names ``temperature`` the request is retried once without
                       it and the client stops sending it.
max_tokens_param       Body key for the output budget. Default
                       ``max_completion_tokens`` for ``openai`` and
                       ``max_tokens`` for ``openai_compatible``.
json_response_format   Send ``response_format: {"type": "json_object"}`` in
                       json_mode (default true). Set false for servers that
                       reject it; the prompt still asks for JSON.
reasoning_effort       Optional ``reasoning_effort`` for OpenAI reasoning models
                       (``minimal`` / ``low`` / ``medium`` / ``high``). Not sent
                       unless set.
extra_body             Optional dict merged into the request body (provider
                       specific options, e.g. OpenRouter ``provider`` routing).
extra_headers          Optional dict of extra request headers (e.g. OpenRouter's
                       ``HTTP-Referer`` / ``X-Title``).
timeout                HTTP timeout in seconds (default 120).
api_key / api_key_env  Credential override (see above).
api_key_required       ``openai_compatible`` only: false = send no
                       ``Authorization`` header when no key is configured.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from ..context import MissingCredentialError
from ..http import HttpError
from ..utils import get_path
from .base import LLMClient, LLMConfigError, LLMError, LLMTruncatedError

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_PATH = "/chat/completions"
DEFAULT_TIMEOUT = 120.0
OPENAI_HOSTS = ("api.openai.com",)

JSON_HINT = "Respond with a single valid JSON object."


# --- errors shared by the HTTP LLM clients ------------------------------------------------
# (duck-typed flags so callers can react without importing these classes)

# LLMConfigError / LLMTruncatedError live in .base; re-exported here for compatibility.


def effective_temperature(config: Dict[str, Any], temperature: Optional[float]) -> Optional[float]:
    """Explicit argument > config ``temperature`` > None (= provider default, not sent)."""
    if temperature is not None:
        return temperature
    cfg = config.get("temperature")
    if cfg is None or cfg == "":
        return None
    try:
        return float(cfg)
    except (TypeError, ValueError):
        raise LLMConfigError(f"invalid temperature {cfg!r} (expected a number)") from None


def timeout_from(config: Dict[str, Any], default: float = DEFAULT_TIMEOUT) -> float:
    try:
        value = float(config.get("timeout") or default)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def post_json(client: LLMClient, url: str, headers: Dict[str, str], body: Dict[str, Any],
              timeout: float, label: str) -> Any:
    """POST ``body`` and decode the JSON reply.

    If the server rejects the request with HTTP 400 because of ``temperature``
    (current OpenAI reasoning models and Claude models do), retry once without
    it and remember not to send it again from this client instance.
    """
    try:
        resp = client.http.post(url, json=body, headers=headers, timeout=timeout)
    except HttpError as e:
        if e.status == 400 and "temperature" in body and "temperature" in (e.body or "").lower():
            client.log.warning("%s: model %s rejected 'temperature'; retrying without it",
                               label, body.get("model"))
            setattr(client, "_drop_temperature", True)
            body = {k: v for k, v in body.items() if k != "temperature"}
            resp = client.http.post(url, json=body, headers=headers, timeout=timeout)
        else:
            raise
    try:
        return resp.json()
    except ValueError as e:
        snippet = (getattr(resp, "text", "") or "")[:200]
        raise LLMError(f"{label}: response is not JSON: {snippet!r}") from e


def _join_text(content: Any) -> str:
    """Message content as text: a string, or a list of ``{"type": "text", "text": ...}`` parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type") in (None, "text", "output_text"):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


class OpenAIClient(LLMClient):
    """Chat Completions client for OpenAI and OpenAI-compatible APIs (see module docstring)."""

    name = "openai"
    env_key = "OPENAI_API_KEY"
    default_model = "gpt-5-mini"

    # --- configuration -------------------------------------------------------------------
    @property
    def kind(self) -> str:
        return str(self.config.get("type") or "openai")

    @property
    def compatible(self) -> bool:
        return self.kind == "openai_compatible"

    @property
    def model(self) -> str:
        m = self.config.get("model")
        if m:
            return str(m)
        return "" if self.compatible else self.default_model

    @property
    def base_url(self) -> str:
        url = str(self.config.get("base_url") or "").strip()
        if not url and not self.compatible:
            url = DEFAULT_BASE_URL
        return url.rstrip("/")

    @property
    def endpoint(self) -> str:
        path = str(self.config.get("path") or DEFAULT_PATH)
        if not path.startswith("/"):
            path = "/" + path
        return self.base_url + path

    @property
    def host(self) -> str:
        return (urlparse(self.base_url).hostname or "").lower()

    def check_config(self) -> None:
        """Raise ``LLMConfigError`` if the client cannot possibly work."""
        if self.compatible:
            missing = [k for k, v in (("base_url", self.base_url), ("model", self.model)) if not v]
            if missing:
                raise LLMConfigError(
                    "openai_compatible: " + " and ".join(missing) + " required "
                    "(set writer.base_url, e.g. https://openrouter.ai/api/v1, and writer.model)")
        if self.base_url and not self.base_url.startswith(("http://", "https://")):
            raise LLMConfigError(f"{self.kind}: base_url must start with http:// or https:// "
                                 f"(got {self.base_url!r})")

    def api_key(self) -> str:
        """Resolve the API key (lazily; see module docstring). '' = send no auth header."""
        explicit = bool(self.config.get("api_key") or self.config.get("api_key_env"))
        optional = self.compatible and self.config.get("api_key_required") is False
        if self.compatible and not explicit and self.host not in OPENAI_HOSTS:
            if optional:
                return ""
            raise MissingCredentialError(
                f"openai_compatible: missing credential (set writer.api_key_env to the env var "
                f"holding the API key for {self.host or 'the endpoint'}; OPENAI_API_KEY is only "
                f"sent to api.openai.com)")
        if optional:
            return self.secret(required=False)
        return self.secret()

    # --- request -------------------------------------------------------------------------
    def build_request(self, system: str, user: str, *, json_mode: bool = False,
                      max_tokens: int = 1500,
                      temperature: Optional[float] = None) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
        """Return ``(url, headers, body)`` for one completion (no network)."""
        self.check_config()
        key = self.api_key()
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        extra_headers = self.config.get("extra_headers")
        if isinstance(extra_headers, dict):
            headers.update({str(k): str(v) for k, v in extra_headers.items()})

        system = system or ""
        if json_mode and "json" not in (system + " " + (user or "")).lower():
            # OpenAI rejects response_format=json_object unless a message mentions JSON.
            system = (system.rstrip() + "\n\n" + JSON_HINT).strip()
        messages: List[Dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user or ""})

        token_key = str(self.config.get("max_tokens_param")
                        or ("max_tokens" if self.compatible else "max_completion_tokens"))
        body: Dict[str, Any] = {"model": self.model, "messages": messages, token_key: int(max_tokens)}
        if json_mode and self.config.get("json_response_format", True) is not False:
            body["response_format"] = {"type": "json_object"}
        temp = effective_temperature(self.config, temperature)
        if temp is not None and not getattr(self, "_drop_temperature", False):
            body["temperature"] = temp
        if self.config.get("reasoning_effort"):
            body["reasoning_effort"] = str(self.config["reasoning_effort"])
        extra_body = self.config.get("extra_body")
        if isinstance(extra_body, dict):
            body.update(extra_body)
        return self.endpoint, headers, body

    def complete(self, system: str, user: str, *, json_mode: bool = False,
                 max_tokens: int = 1500, temperature: Optional[float] = None) -> str:
        url, headers, body = self.build_request(system, user, json_mode=json_mode,
                                                max_tokens=max_tokens, temperature=temperature)
        data = post_json(self, url, headers, body, timeout_from(self.config), self.kind)
        return self.parse_response(data)

    # --- response ------------------------------------------------------------------------
    def parse_response(self, data: Any) -> str:
        label = f"{self.kind} ({self.model})"
        if not isinstance(data, dict):
            raise LLMError(f"{label}: unexpected response {str(data)[:200]!r}")
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            err = get_path(data, "error.message") or data.get("error") or data.get("detail")
            detail = f": {err}" if err else f": {json.dumps(data)[:200]}"
            raise LLMError(f"{label}: response has no choices{detail}")
        choice = choices[0]
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        refusal = message.get("refusal")
        if refusal:
            raise LLMError(f"{label}: model refused: {str(refusal)[:200]}")
        finish = str(choice.get("finish_reason") or "")
        if finish == "content_filter":
            raise LLMError(f"{label}: output blocked by the provider's content filter")
        text = _join_text(message.get("content"))
        if finish == "length":
            raise LLMTruncatedError(f"{label}: output truncated (finish_reason=length); "
                                    f"raise max_tokens")
        if not text.strip():
            raise LLMError(f"{label}: empty model output (finish_reason={finish or 'none'})")
        return text


__all__ = ["OpenAIClient", "LLMConfigError", "LLMTruncatedError", "effective_temperature",
           "post_json", "timeout_from"]
