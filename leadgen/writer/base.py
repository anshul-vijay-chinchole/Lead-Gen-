"""Base class for writers.

``write(lead)`` returns a ``WriterOutput`` with one ``Message`` per step of
``playbook.writer.sequence`` (same order, ``day`` copied from the step).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from ..context import Adapter
from ..models import Lead, Message


@dataclass
class WriterOutput:
    messages: List[Message] = field(default_factory=list)
    personalization: str = ""
    hypothesis: str = ""
    writer: str = ""
    warnings: List[str] = field(default_factory=list)


class Writer(Adapter):
    name = "writer"

    def write(self, lead: Lead) -> WriterOutput:  # pragma: no cover - interface
        raise NotImplementedError
