"""TheirStack job-search API as a hiring-signal source (with company data + hiring teams).

``POST https://api.theirstack.com/v1/jobs/search`` page by page (TheirStack
pages are 0-based). Each job becomes a ``job_posting`` signal on its company
(``company_object``: name, domain, industry, employee count, LinkedIn, HQ
city/country, description); ``hiring_team`` members become ``Contact``s
(``title`` = their ``role``). Jobs of the same company are grouped.

Credential: ``THEIRSTACK_API_KEY`` (or config ``api_key`` / ``api_key_env``),
sent as ``Authorization: Bearer <key>``. TheirStack charges credits per job
returned, so the page size and page count are capped.

Config keys
-----------
job_titles          ``job_title_or`` (any of these titles).
exclude_job_titles  ``job_title_not``.
countries           ``job_country_code_or`` (ISO-3166 alpha-2 codes, upper-cased).
max_age_days        ``posted_at_max_age_days`` (default 30).
min_employees, max_employees
                    ``min_employee_count`` / ``max_employee_count``; when unset and
                    ``use_icp`` (default true) the playbook's ``icp.employees``
                    are used as search hints.
filters             Raw request-body fields merged in as-is (any TheirStack filter,
                    e.g. ``job_description_pattern_or``, ``company_technology_slug_or``).
limit               Jobs per page - TheirStack's own ``limit`` (default 25, max 500).
                    (``per_page`` is accepted as an alias.)
max_pages           Default 2.
max_companies       Max companies returned (0 = no cap beyond limit x max_pages).
mapping             Overrides of the job -> Company mapping (canonical field ->
                    dotted path into the job object).
people              Override the hiring-team extraction ``{path, mapping}``
                    (default path ``hiring_team``); ``people: null`` disables it.
base_url            Default ``https://api.theirstack.com/v1``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..http import HttpError
from ..usage import BudgetExceeded
from ..models import Company, SignalType
from ..utils import get_path, to_int
from .base import Source
from .mapping import CompanyCollector, RecordMapper, is_blank, merge_mappings

DEFAULT_BASE_URL = "https://api.theirstack.com/v1"
SEARCH_PATH = "/jobs/search"
MAX_PAGE_SIZE = 500

DEFAULT_MAPPING: Dict[str, Any] = {
    "name": ["company_object.name", "company"],
    "domain": ["company_domain", "company_object.domain"],
    "website": ["company_object.url", "company_url"],
    "linkedin_url": ["company_object.linkedin_url", "company_linkedin_url"],
    "industry": "company_object.industry",
    "employees": ["company_object.employee_count", "company_employee_count"],
    "city": "company_object.city",
    "country": ["company_object.country", "company_object.country_code"],
    "description": ["company_object.long_description", "company_object.seo_description"],
    "keywords": ["company_object.keywords", "company_object.technology_names"],
    "signal_title": "job_title",
    "signal_url": ["final_url", "url", "source_url"],
    "signal_date": ["date_posted", "discovered_at"],
    "signal_location": ["location", "short_location", "long_location"],
    "signal_description": "description",
    "signal_id": "id",
    "signal_data.remote": "remote",
    "signal_data.hybrid": "hybrid",
    "signal_data.country_code": "country_code",
    "signal_data.salary": "salary_string",
    "signal_data.seniority": "seniority",
    "signal_data.employment_statuses": "employment_statuses",
    "data.theirstack_company_id": "company_object.id",
}

DEFAULT_PEOPLE: Dict[str, Any] = {
    "path": "hiring_team",
    "mapping": {"full_name": "full_name", "first_name": "first_name", "linkedin_url": "linkedin_url",
                "title": "role"},
}


def _as_list(value: Any) -> List[str]:
    if is_blank(value):
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if not is_blank(v) and str(v).strip()]
    return [str(value).strip()]


class TheirStackSource(Source):
    """TheirStack job search (see module docstring for config)."""

    name = "theirstack"
    env_key = "THEIRSTACK_API_KEY"

    @property
    def limit(self) -> int:
        """Max companies (``max_companies``) - ``limit`` is TheirStack's page size here."""
        try:
            return int(self.config.get("max_companies") or 0)
        except (TypeError, ValueError):
            return 0

    def _int(self, key: str, default: Optional[int]) -> Optional[int]:
        raw = self.config.get(key)
        if raw is None or raw == "":
            return default
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise ValueError(f"source {self.label}: '{key}' must be an integer, got {raw!r}") from None

    @property
    def page_size(self) -> int:
        size = self._int("per_page", None) or self._int("limit", None) or 25
        return max(1, min(MAX_PAGE_SIZE, size))

    @property
    def url(self) -> str:
        base = str(self.config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        return base + str(self.config.get("search_path") or SEARCH_PATH)

    def _employees(self) -> Dict[str, int]:
        lo, hi = self._int("min_employees", None), self._int("max_employees", None)
        if lo is None and hi is None and self.config.get("use_icp", True):
            emp = self.ctx.playbook.icp.get("employees") or {}
            if isinstance(emp, dict):
                lo, hi = to_int(emp.get("min")), to_int(emp.get("max"))
        out: Dict[str, int] = {}
        if lo is not None:
            out["min_employee_count"] = lo
        if hi is not None:
            out["max_employee_count"] = hi
        return out

    def search_body(self, page: int) -> Dict[str, Any]:
        body: Dict[str, Any] = {"posted_at_max_age_days": self._int("max_age_days", 30)}
        titles = _as_list(self.config.get("job_titles"))
        if titles:
            body["job_title_or"] = titles
        excluded = _as_list(self.config.get("exclude_job_titles"))
        if excluded:
            body["job_title_not"] = excluded
        countries = [c.upper() for c in _as_list(self.config.get("countries"))]
        if countries:
            body["job_country_code_or"] = countries
        body.update(self._employees())
        filters = self.config.get("filters") or {}
        if not isinstance(filters, dict):
            raise ValueError(f"source {self.label}: 'filters' must be a mapping of TheirStack search fields")
        body.update(filters)
        body["page"] = page
        body["limit"] = self.page_size
        return body

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.secret()}", "Content-Type": "application/json",
                "Accept": "application/json"}

    def fetch(self) -> List[Company]:
        if self.ctx.dry_run:
            self.log.info("dry-run: source %s (TheirStack) skipped", self.label)
            return []
        headers = self._headers()
        people = self.config["people"] if "people" in self.config else DEFAULT_PEOPLE
        mapper = RecordMapper(merge_mappings(DEFAULT_MAPPING, self.config.get("mapping")), label=self.label,
                              today=self.ctx.today, defaults={"signal_type": SignalType.JOB_POSTING},
                              people=people, default_signal=self.config.get("default_signal"))
        collector = CompanyCollector(self.limit)
        max_pages = max(1, self._int("max_pages", 2) or 1)
        size = self.page_size
        seen_jobs = 0
        pages = 0
        for page in range(max_pages):
            if collector.full:
                break
            try:
                data = self.http.post_json(self.url, json=self.search_body(page), headers=headers)
            except (HttpError, ValueError) as e:
                if page == 0:
                    raise
                self.log.warning("source %s: page %d failed (%s); keeping %d companies",
                                 self.label, page, e, len(collector))
                break
            except BudgetExceeded as e:
                if page == 0:
                    raise  # nothing was paid for yet
                self.budget_stop = str(e)
                self.log.warning("source %s: %s; keeping %d companies", self.label, e, len(collector))
                break
            pages += 1
            if not isinstance(data, dict):
                raise ValueError(f"source {self.label}: unexpected TheirStack response ({type(data).__name__})")
            jobs = [j for j in data.get("data") or [] if isinstance(j, dict)]
            for job in jobs:
                collector.add(mapper.to_company(job))
            seen_jobs += len(jobs)
            total = to_int(get_path(data, "metadata.total_results"))
            if len(jobs) < size or (total is not None and seen_jobs >= total):
                break
        self.log.info("source %s: %d jobs -> %d companies (%d page(s))", self.label, seen_jobs,
                      len(collector), pages)
        return collector.companies


__all__ = ["TheirStackSource", "DEFAULT_MAPPING", "DEFAULT_PEOPLE"]
