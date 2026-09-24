"""Copywriters: turn a scored lead into a personalised multi-step sequence.

``build_writer(ctx)`` picks the writer for a run: the AI writer when
``writer.type == "ai"``, the run is not a dry-run and an LLM is configured
(``ctx.llm``); the offline template writer otherwise (logging why when AI was
requested). Both return one ``Message`` per ``writer.sequence`` step.
"""
from __future__ import annotations

from typing import Any

from .base import Writer, WriterOutput


def build_writer(ctx: Any) -> Writer:
    """Return the writer the pipeline should use for this run."""
    from .. import registry  # local import: adapters load lazily

    wcfg = dict(ctx.playbook.writer or {})
    if str(wcfg.get("type") or "template") == "ai":
        if ctx.dry_run:
            ctx.log.info("writer: dry-run, using the template writer instead of the AI writer (no LLM calls)")
        elif ctx.llm is None:
            provider = wcfg.get("provider") or "(none)"
            ctx.log.warning("writer: writer.type is 'ai' but no LLM is available (provider %s; check "
                            "writer.provider and its API key) - using the template writer", provider)
        else:
            return registry.create("writer", dict(wcfg, type="ai"), ctx)
    return registry.create("writer", dict(wcfg, type="template"), ctx)


__all__ = ["Writer", "WriterOutput", "build_writer"]
