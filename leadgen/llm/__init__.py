"""LLM clients (OpenAI, Anthropic, any OpenAI-compatible endpoint)."""
from __future__ import annotations

from typing import Any, Optional

from .base import LLMClient, LLMError, parse_json_block


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
    cfg = {"type": provider, "model": w.get("model"), "base_url": w.get("base_url"),
           "temperature": w.get("temperature")}
    if w.get("api_key_env"):
        cfg["api_key_env"] = w["api_key_env"]
    return registry.create("llm", cfg, ctx)


__all__ = ["LLMClient", "LLMError", "build_llm", "parse_json_block"]
