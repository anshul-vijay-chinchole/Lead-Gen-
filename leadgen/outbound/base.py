"""Base class for exporters (files) and senders (API pushes).

``export(leads, out_dir)`` receives leads according to ``scope`` (see below)
and returns an ``ExportResult``. ``is_send`` = True means
the exporter actually hands leads to a sending tool (the pipeline then marks
them EXPORTED and records them for dedupe); file exporters leave it False.
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
