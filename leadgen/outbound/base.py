"""Base class for exporters (files) and senders (API pushes).

``export(leads, out_dir)`` receives leads according to ``scope`` (see below)
and returns an ``ExportResult``. The pipeline decides hand-over by ``scope``:
every ``scope = "outbound"`` exporter (API pushes AND upload files such as
instantly_csv) marks what it exported as handed over, so nobody is sent the
sequence twice. ``is_send`` is informational (True = talks to a sending API).
In ``ctx.dry_run`` senders must not call the network.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from ..context import Adapter
from ..models import Lead


@dataclass
class ExportResult:
    exporter: str
    count: int = 0
    path: str = ""
    detail: str = ""
    exported_ids: List[str] = field(default_factory=list)  # lead ids actually handed over


class Exporter(Adapter):
    name = "exporter"
    is_send = False
    # "all": receives every scored lead (review sheets). "outbound": receives only
    # send-eligible leads, and the pipeline marks what it exported as handed over
    # (so the next run never hands the same person over twice).
    scope = "all"

    def export(self, leads: List[Lead], out_dir: Path) -> ExportResult:  # pragma: no cover
        raise NotImplementedError
