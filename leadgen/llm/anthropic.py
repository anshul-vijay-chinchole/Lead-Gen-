"""Anthropic Messages API client (``type: anthropic``).

Request::

    POST https://api.anthropic.com/v1/messages
    x-api-key: <key>
    anthropic-version: 2023-06-01
    content-type: application/json
    {"model": "claude-sonnet-5", "max_tokens": 1500, "system": SYSTEM,
     "messages": [{"role": "user", "content": USER}],
     "temperature": 0.7}                                  # only when not None

Response (documented shape)::

    {"id": "msg_...", "type": "message", "role": "assistant", "model": "claude-sonnet-5",
     "content": [{"type": "thinking", "thinking": "", "signature": "..."},
                 {"type": "text", "text": "..."}],
     "stop_reason": "end_turn", "stop_sequence": null,
     "usage": {"input_tokens": 812, "output_tokens": 403}}

``complete`` joins the text of every ``type: "text"`` content block (thinking /
tool blocks are ignored - current Claude models think adaptively by default,
and that thinking counts against ``max_tokens``). ``stop_reason: "max_tokens"``
raises ``LLMTruncatedError`` ("output truncated"), ``stop_reason: "refusal"``
and empty text raise ``LLMError``; HTTP failures surface as
``leadgen.http.HttpError``.

JSON mode: the Messages API has no ``response_format`` flag and current models
do not accept an assistant prefill, so ``json_mode`` appends an instruction to
the system prompt asking for a single JSON object and nothing else;
``complete_json`` (base class) then tolerates stray fences / prose.

Credential: ``ANTHROPIC_API_KEY`` (config ``api_key`` / ``api_key_env`` override),
resolved lazily at the first request.

Config keys
-----------
model              Model id (default ``claude-sonnet-5``).
base_url           Endpoint override. Accepts the API root
                   (``https://api.anthropic.com``), the ``/v1`` root, or the full
                   ``.../v1/messages`` URL (e.g. for a gateway / proxy).
temperature        Default temperature when ``complete`` gets ``temperature=None``;
                   only sent when not None. Current Claude models (Sonnet 5,
                   Opus 4.7+) reject sampling parameters: on an HTTP 400 naming
                   ``temperature`` the request is retried once without it and
                   the client stops sending it.
anthropic_version  ``anthropic-version`` header (default ``2023-06-01``).
effort             Optional ``output_config.effort`` (``low`` / ``medium`` /
                   ``high`` ...) on models that support it - lower effort means
                   less thinking and cheaper copy. Not sent unless set.
extra_body         Optional dict merged into the request body.
extra_headers      Optional dict of extra headers (e.g. ``anthropic-beta``).
timeout            HTTP timeout in seconds (default 120).
api_key / api_key_env  Credential override.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from ..utils import get_path
from .base import LLMClient, LLMError
from .openai import LLMConfigError, LLMTruncatedError, effective_temperature, post_json, timeout_from

DEFAULT_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
JSON_INSTRUCTION = ("Answer with a single JSON object and nothing else: no prose before or after "
                    "it and no markdown code fences.")


def messages_url(base_url: str) -> str:
    """Normalise a configured base URL to the ``/v1/messages`` endpoint."""
    url = (base_url or "").strip().rstrip("/")
    if not url:
        return DEFAULT_URL
    if url.endswith("/messages"):
        return url
    if url.endswith("/v1"):
        return url + "/messages"
    return url + "/v1/messages"


class AnthropicClient(LLMClient):
    """Claude via the Messages API (see module docstring)."""

    name = "anthropic"
    env_key = "ANTHROPIC_API_KEY"
    default_model = "claude-sonnet-5"

    @property
    def endpoint(self) -> str:
        return messages_url(str(self.config.get("base_url") or ""))

    def build_request(self, system: str, user: str, *, json_mode: bool = False,
                      max_tokens: int = 1500,
                      temperature: Optional[float] = None) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
        """Return ``(url, headers, body)`` for one completion (no network)."""
        url = self.endpoint
        if not url.startswith(("http://", "https://")):
            raise LLMConfigError(f"anthropic: base_url must start with http:// or https:// (got {url!r})")
        headers: Dict[str, str] = {
            "x-api-key": self.secret(),
            "anthropic-version": str(self.config.get("anthropic_version") or ANTHROPIC_VERSION),
            "content-type": "application/json",
        }
        extra_headers = self.config.get("extra_headers")
        if isinstance(extra_headers, dict):
            headers.update({str(k): str(v) for k, v in extra_headers.items()})

        system = (system or "").strip()
        if json_mode:
            system = (system + "\n\n" + JSON_INSTRUCTION).strip()
        body: Dict[str, Any] = {"model": self.model, "max_tokens": int(max_tokens)}
        if system:
            body["system"] = system
        body["messages"] = [{"role": "user", "content": user or ""}]
        temp = effective_temperature(self.config, temperature)
        if temp is not None and not getattr(self, "_drop_temperature", False):
            body["temperature"] = temp
        if self.config.get("effort"):
            body["output_config"] = {"effort": str(self.config["effort"])}
        extra_body = self.config.get("extra_body")
        if isinstance(extra_body, dict):
            body.update(extra_body)
        return url, headers, body

    def complete(self, system: str, user: str, *, json_mode: bool = False,
                 max_tokens: int = 1500, temperature: Optional[float] = None) -> str:
        url, headers, body = self.build_request(system, user, json_mode=json_mode,
                                                max_tokens=max_tokens, temperature=temperature)
        data = post_json(self, url, headers, body, timeout_from(self.config), self.name)
        return self.parse_response(data)

    def parse_response(self, data: Any) -> str:
        label = f"anthropic ({self.model})"
        if not isinstance(data, dict):
            raise LLMError(f"{label}: unexpected response {str(data)[:200]!r}")
        if data.get("type") == "error" or (isinstance(data.get("error"), dict) and "content" not in data):
            err = get_path(data, "error.message") or json.dumps(data)[:200]
            raise LLMError(f"{label}: API error: {err}")
        stop = str(data.get("stop_reason") or "")
        blocks = data.get("content")
        parts: List[str] = []
        if isinstance(blocks, list):
            for block in blocks:
                if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])
        text = "".join(parts)
        if stop == "max_tokens":
            raise LLMTruncatedError(f"{label}: output truncated (stop_reason=max_tokens); raise max_tokens")
        if stop == "refusal":
            category = get_path(data, "stop_details.category")
            explanation = get_path(data, "stop_details.explanation")
            detail = ", ".join(str(x) for x in (category, explanation) if x)
            raise LLMError(f"{label}: model refused the request" + (f" ({detail})" if detail else ""))
        if not text.strip():
            raise LLMError(f"{label}: empty model output (stop_reason={stop or 'none'})")
        return text


__all__ = ["AnthropicClient", "messages_url"]
