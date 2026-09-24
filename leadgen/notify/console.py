"""Console notifier (``type: console``): print alerts to the terminal.

This is the default channel (``notify.channels: [{type: console}]``) and the
only notifier whose job *is* terminal output, so it writes straight to
``sys.stdout`` (or ``sys.stderr``). It never touches the network, so it also
runs in ``--dry-run``.

Output is a readable block::

    ========================================================================
    [POSITIVE] Positive reply: Jane Doe (Acme)
    ------------------------------------------------------------------------
    From: Jane Doe <jane@acme.com>
    ...
    ========================================================================

Config keys
-----------
stream      ``stdout`` (default) or ``stderr``.
width       Width of the separator lines (default 72, clamped to 20..200).
show_data   When true, also print the event's ``data`` dict as indented JSON
            (default false - the text is meant to be self-contained).
events      Optional list of event names this channel accepts; other events
            are silently ignored (default: every event enabled in
            ``notify.on``).
"""
from __future__ import annotations

import json
import sys
from typing import Any, Dict, TextIO

from .base import Notifier


def _event_label(event: str) -> str:
    return str(event or "").replace("_", " ").strip().upper()


class ConsoleNotifier(Notifier):
    """Write ``title`` + ``text`` as a framed block to stdout (see module docstring)."""

    name = "console"
    offline = True

    def _stream(self) -> TextIO:
        # Resolved at send time (not import time) so redirected/captured streams work.
        return sys.stderr if str(self.config.get("stream") or "").lower() == "stderr" else sys.stdout

    def _width(self) -> int:
        try:
            width = int(self.config.get("width") or 72)
        except (TypeError, ValueError):
            width = 72
        return max(20, min(200, width))

    def accepts(self, event: str) -> bool:
        events = self.config.get("events")
        return not events or event in events

    def format(self, event: str, title: str, text: str, data: Dict[str, Any]) -> str:
        width = self._width()
        label = _event_label(event)
        heading = f"[{label}] {title}".strip() if label else str(title or "").strip()
        lines = ["=" * width, heading or "(no title)"]
        body = str(text or "").rstrip()
        if body:
            lines += ["-" * width, body]
        if self.config.get("show_data") and data:
            lines += ["-" * width, json.dumps(data, indent=2, default=str, ensure_ascii=False)]
        lines.append("=" * width)
        return "\n".join(lines) + "\n"

    def send(self, event: str, title: str, text: str, data: Dict[str, Any]) -> None:
        if not self.accepts(event):
            return
        stream = self._stream()
        stream.write(self.format(event, title, text, data or {}))
        stream.flush()


__all__ = ["ConsoleNotifier"]
