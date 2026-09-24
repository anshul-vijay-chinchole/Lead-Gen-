"""LLM clients (OpenAI, Anthropic, any OpenAI-compatible endpoint)."""
from __future__ import annotations

from typing import Any, Optional

from .base import LLMClient, LLMConfigError, LLMError, LLMTruncatedError, parse_json_block


def build_llm(ctx: Any) -> Optional[LLMClient]:
    """Build the LLM client configured in ``playbook.writer`` (provider/model/...).

    Returns None when no provider is configured. Raises MissingCredentialError
    when a provider is configured but its key is missing.
    """
    from .. import registry

    w = ctx.playbook.writer
    provider = w.get("provider")
    if not provider:
        return None
    # writer.llm holds optional client settings (timeout, reasoning_effort, effort,
    # extra_body, extra_headers, api_key_required, json_response_format, ...).
    cfg = dict(w.get("llm") or {})
    cfg.update({"type": provider, "model": w.get("model"), "base_url": w.get("base_url"),
                "temperature": w.get("temperature")})
    if w.get("api_key_env"):
        cfg["api_key_env"] = w["api_key_env"]
    return registry.create("llm", cfg, ctx)


__all__ = ["LLMClient", "LLMConfigError", "LLMError", "LLMTruncatedError", "build_llm", "parse_json_block"]
