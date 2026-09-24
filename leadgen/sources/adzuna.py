"""Adzuna job-search API as a hiring-signal source.

``GET https://api.adzuna.com/v1/api/jobs/{country}/search/{page}`` for every
configured country x query, newest first. Every result becomes one
``job_posting`` signal; results are grouped per employer
(``company.display_name``) into ``Company`` objects whose ``location`` is the
job location and ``country`` the searched country code (upper-case). Results
without an employer name are skipped.

Credentials: ``ADZUNA_APP_ID`` + ``ADZUNA_APP_KEY`` (or config ``app_id`` /
``app_key`` / ``app_id_env`` / ``app_key_env``), sent as query parameters
(``HttpClient`` redacts ``app_key`` from logs).

Config keys
-----------
countries / country   Adzuna country codes to search (default ``["us"]``; ``uk`` is
                      accepted for ``gb``).
queries               List of ``what`` strings; each one is searched separately
                      (``what`` / ``query`` accepted for a single one). Without any,
                      one unfiltered search per country runs (still bounded).
what_or, what_exclude, where, category
                      Passed through to Adzuna (lists are space-joined).
max_days_old          Default 30.
results_per_page      Default 50 (Adzuna's maximum).
max_pages             Pages per country x query, default 2.
params                Extra raw query parameters (e.g. ``{distance: 25, full_time: 1}``).
limit                 Max companies returned (0 = no cap beyond max_pages).
mapping               Overrides of the result -> Company mapping (canonical field
                      -> dotted path, e.g. ``signal_data.salary: salary_max``).
base_url              Default ``https://api.adzuna.com/v1/api/jobs``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..http import HttpError
from ..models import Company, SignalType
from ..utils import to_int
from .base import Source
from .mapping import CompanyCollector, RecordMapper, is_blank, merge_mappings

DEFAULT_BASE_URL = "https://api.adzuna.com/v1/api/jobs"
MAX_RESULTS_PER_PAGE = 50
_COUNTRY_ALIASES = {"uk": "gb"}

DEFAULT_MAPPING: Dict[str, Any] = {
    "name": "company.display_name",
    "location": "location.display_name",
    "signal_title": "title",
    "signal_description": "description",
    "signal_date": "created",
    "signal_url": "redirect_url",
    "signal_id": "id",
    "signal_location": "location.display_name",
    "signal_data.category": "category.label",
    "signal_data.salary_min": "salary_min",
    "signal_data.salary_max": "salary_max",
    "signal_data.salary_is_predicted": "salary_is_predicted",
    "signal_data.contract_type": "contract_type",
    "signal_data.contract_time": "contract_time",
    "signal_data.area": "location.area",
}


def _as_list(value: Any) -> List[str]:
    if is_blank(value):
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if not is_blank(v) and str(v).strip()]
    return [str(value).strip()]


def _join(value: Any) -> Optional[str]:
    items = _as_list(value)
    return " ".join(items) if items else None


class AdzunaSource(Source):
    """Adzuna job search (see module docstring for config)."""

    name = "adzuna"
    env_key = "ADZUNA_APP_KEY"

    @property
    def base_url(self) -> str:
        return str(self.config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")

    def _int(self, key: str, default: int) -> int:
        raw = self.config.get(key)
        if raw is None or raw == "":
            return default
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise ValueError(f"source {self.label}: '{key}' must be an integer, got {raw!r}") from None

    @property
    def countries(self) -> List[str]:
        raw = self.config.get("countries") or self.config.get("country") or ["us"]
        out = []
        for c in _as_list(raw):
            code = _COUNTRY_ALIASES.get(c.lower(), c.lower())
            if code not in out:
                out.append(code)
        return out

    @property
    def queries(self) -> List[Optional[str]]:
        qs = _as_list(self.config.get("queries")) or _as_list(self.config.get("what")) \
            or _as_list(self.config.get("query"))
        return list(qs) if qs else [None]

    @property
    def results_per_page(self) -> int:
        return max(1, min(MAX_RESULTS_PER_PAGE, self._int("results_per_page", MAX_RESULTS_PER_PAGE)))

    def params(self, what: Optional[str]) -> Dict[str, Any]:
        """Query parameters for one search (credentials included)."""
        p: Dict[str, Any] = {
            "app_id": self.secret("app_id", "ADZUNA_APP_ID"),
            "app_key": self.secret("app_key", "ADZUNA_APP_KEY"),
            "results_per_page": self.results_per_page,
            "max_days_old": self._int("max_days_old", 30),
            "sort_by": "date",
            "content-type": "application/json",
        }
        optional = {"what": what, "what_or": _join(self.config.get("what_or")),
                    "what_exclude": _join(self.config.get("what_exclude")),
                    "where": self.config.get("where"), "category": self.config.get("category")}
        for k, v in optional.items():
            if not is_blank(v):
                p[k] = v
        extra = self.config.get("params") or {}
        if not isinstance(extra, dict):
            raise ValueError(f"source {self.label}: 'params' must be a mapping of query parameters")
        p.update(extra)
        return p

    def fetch(self) -> List[Company]:
        if self.ctx.dry_run:
            self.log.info("dry-run: source %s (Adzuna) skipped", self.label)
            return []
        mapper = RecordMapper(merge_mappings(DEFAULT_MAPPING, self.config.get("mapping")), label=self.label,
                              today=self.ctx.today, defaults={"signal_type": SignalType.JOB_POSTING},
                              default_signal=self.config.get("default_signal"))
        collector = CompanyCollector(self.limit)
        max_pages = max(1, self._int("max_pages", 2))
        rpp = self.results_per_page
        searches = failures = skipped = 0
        last_error: Optional[Exception] = None
        for country in self.countries:
            for what in self.queries:
                if collector.full:
                    break
                searches += 1
                for page in range(1, max_pages + 1):
                    url = f"{self.base_url}/{country}/search/{page}"
                    try:
                        data = self.http.get_json(url, params=self.params(what))
                    except (HttpError, ValueError) as e:
                        if isinstance(e, HttpError) and e.status in (401, 403):
                            raise
                        self.log.warning("source %s: search %s/%r page %d failed: %s",
                                         self.label, country, what, page, e)
                        if page == 1:
                            failures += 1
                            last_error = e
                        break
                    results = (data or {}).get("results") if isinstance(data, dict) else None
                    results = [r for r in results or [] if isinstance(r, dict)]
                    for r in results:
                        company = mapper.to_company(r, {"country": country.upper()})
                        if company is None:
                            skipped += 1
                            continue
                        collector.add(company)
                    if collector.full or len(results) < rpp:
                        break
                    count = to_int(data.get("count"))
                    if count is not None and page * rpp >= count:
                        break
        if searches and failures == searches and last_error is not None:
            raise last_error
        if skipped:
            self.log.info("source %s: skipped %d jobs without an employer name", self.label, skipped)
        self.log.info("source %s: %d companies", self.label, len(collector))
        return collector.companies


__all__ = ["AdzunaSource", "DEFAULT_MAPPING"]
