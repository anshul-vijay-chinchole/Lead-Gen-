"""Notifiers + the ``notify`` dispatcher used by the rest of the engine."""
from __future__ import annotations

from typing import Any, Dict, Optional

from .base import Notifier


def notify(ctx: Any, event: str, title: str, text: str,
           data: Optional[Dict[str, Any]] = None) -> int:
    """Send an event to every configured channel if the event is enabled.

    Returns the number of channels that accepted it. Never raises: a broken
    Slack webhook must not kill a pipeline run.
    """
    from .. import registry

    cfg = ctx.playbook.notify
    if event not in (cfg.get("on") or []):
        return 0
    sent = 0
    for ch in cfg.get("channels") or []:
        if not isinstance(ch, dict) or ch.get("enabled") is False:
            continue
        try:
            registry.create("notifier", ch, ctx).send(event, title, text, data or {})
            sent += 1
        except Exception as e:  # noqa: BLE001 - notifications are best effort
            ctx.log.warning("notify via %s failed: %s", ch.get("type"), e)
    return sent


__all__ = ["Notifier", "notify"]
