"""Base class for sources.

A source turns some external data (CSV export, API, scraper run) into
``Company`` objects, each carrying the ``Signal``s that explain *why now* and,
if the data has them, ``Contact``s. Sources must NOT filter by ICP or score -
later stages do that. They should set ``company.sources = [self.label]`` and
``signal.source = self.label``.
"""
from __future__ import annotations

from typing import List

from ..context import Adapter
from ..models import Company


class Source(Adapter):
    name = "source"

    @property
    def label(self) -> str:
        """Human label used in ``Company.sources`` / ``Signal.source``."""
        return str(self.config.get("label") or self.config.get("type") or self.name)

    @property
    def limit(self) -> int:
        """Max companies to return (config ``limit``; 0 = no limit)."""
        try:
            return int(self.config.get("limit") or 0)
        except (TypeError, ValueError):
            return 0

    def fetch(self) -> List[Company]:  # pragma: no cover - interface
        raise NotImplementedError
