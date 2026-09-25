"""Base class for LLM clients."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from ..context import Adapter


class LLMError(RuntimeError):
    pass


class LLMConfigError(LLMError):
    """The client is misconfigured (missing base_url/model, ...). Retrying will not help."""

    permanent = True


class LLMTruncatedError(LLMError):
    """The model hit the output-token limit before finishing its answer."""

    truncated = True


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_json_block(text: str) -> Dict[str, Any]:
    """Parse a JSON object from model output (tolerates code fences / prose around it)."""
    if not text:
        raise LLMError("empty model output")
    candidates = [text.strip()]
    m = _FENCE.search(text)
    if m:
        candidates.insert(0, m.group(1).strip())
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    for c in candidates:
        try:
            obj = json.loads(c)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict):
            return obj
    raise LLMError(f"model did not return a JSON object: {text[:200]!r}")


class LLMClient(Adapter):
    """``complete(system, user)`` -> text. ``complete_json`` -> dict."""

    name = "llm"
    default_model = ""
    # {"input_tokens": int, "output_tokens": int} of the last successful call (None if unknown)
    last_usage: Optional[Dict[str, int]] = None

    @property
    def model(self) -> str:
        return str(self.config.get("model") or self.default_model)

    def complete(self, system: str, user: str, *, json_mode: bool = False,
                 max_tokens: int = 1500, temperature: Optional[float] = None) -> str:  # pragma: no cover
        raise NotImplementedError

    def complete_json(self, system: str, user: str, *, max_tokens: int = 1500,
                      temperature: Optional[float] = None) -> Dict[str, Any]:
        return parse_json_block(self.complete(system, user, json_mode=True,
                                              max_tokens=max_tokens, temperature=temperature))
