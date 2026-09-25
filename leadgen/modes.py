"""Run modes.

``delivery`` (the default) turns the engine into a lead-delivery product: the
pipeline stops after scoring and exports lead files. ``outbound`` enables the
outreach features: email copy (writer), hand-over to sending tools
(Instantly / Smartlead / webhook), reply handling, follow-ups and the reply
webhook server. Every outbound entry point calls ``require_outbound`` so a
delivery-mode playbook can never send, write or process replies by accident.
"""
from __future__ import annotations

from typing import Any

DELIVERY = "delivery"
OUTBOUND = "outbound"


class OutboundOnlyError(RuntimeError):
    """An outbound-only feature was used with a delivery-mode playbook."""


def mode_of(ctx_or_playbook: Any) -> str:
    pb = getattr(ctx_or_playbook, "playbook", ctx_or_playbook)
    return str(getattr(pb, "mode", DELIVERY) or DELIVERY)


def is_outbound(ctx_or_playbook: Any) -> bool:
    return mode_of(ctx_or_playbook) == OUTBOUND


def outbound_only_message(feature: str, playbook_name: str = "") -> str:
    where = f"playbook '{playbook_name}'" if playbook_name else "this playbook"
    return (f"{feature} is an outbound-mode feature, and {where} runs in delivery mode "
            f"(the default). Add 'mode: outbound' to the playbook to use it.")


def require_outbound(ctx_or_playbook: Any, feature: str) -> None:
    """Raise ``OutboundOnlyError`` unless the playbook runs in outbound mode."""
    if not is_outbound(ctx_or_playbook):
        pb = getattr(ctx_or_playbook, "playbook", ctx_or_playbook)
        raise OutboundOnlyError(outbound_only_message(feature, getattr(pb, "name", "")))
