"""Base class for notifiers (console, Slack, generic webhook)."""
from __future__ import annotations

from typing import Any, Dict

from ..context import Adapter


class Notifier(Adapter):
    name = "notifier"

    def send(self, event: str, title: str, text: str, data: Dict[str, Any]) -> None:  # pragma: no cover
        raise NotImplementedError
