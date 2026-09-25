"""Apollo.io company search as a source (companies + funding / hiring / growth signals).

Calls ``POST {base_url}/api/v1/mixed_companies/search`` (Apollo's organization
search) page by page and maps every organization (and saved account) to a
``Company``. Signals:

* ``funding`` - from ``latest_funding_stage`` / ``latest_funding_round_date``
  (+ the matching ``funding_events`` entry's amount and news URL when present,
  ``total_funding`` in the description), e.g. "Raised Series A ($12M)";
* ``headcount_growth`` - when ``headcount_growth_min`` is set and
  ``organization_headcount_six_month_growth`` (else ``..._twelve_month_growth``)
  reaches it, e.g. "Headcount up 18% in 6 months";
* ``job_posting`` - when ``job_postings: true``: one extra
  ``GET /api/v1/organizations/{id}/job_postings`` per returned company
  (``organization_job_postings``; uses Apollo credits, so it is off by default).

The Apollo organization id is kept in ``company.data['apollo_id']`` (for saved
accounts: their ``organization_id``) so the Apollo contact finder can reuse it.

Credential: ``APOLLO_API_KEY`` (or config ``api_key`` / ``api_key_env``), sent
in the ``x-api-key`` header.

Config keys
-----------
locations          ``organization_locations`` (free text: cities, states, countries).
employee_ranges    ``organization_num_employees_ranges``; accepts ``"11,50"``,
                   ``"11-50"``, ``[11, 50]``, ``{min: 11, max: 50}`` or ``"500+"``.
keywords           ``q_organization_keyword_tags``.
filters            Raw request-body fields merged in as-is (any Apollo filter,
                   e.g. ``organization_ids``, ``revenue_range``, ``q_organization_name``).
use_icp            Default true: when ``locations`` / ``employee_ranges`` are not
                   set, use the playbook's ``icp.locations`` / ``icp.employees``
                   as search hints (sources never *filter* by ICP).
per_page           Page size, max 100 (default: ``limit`` if set, else 25).
max_pages          Default 2.
limit              Max companies returned (0 = no cap beyond max_pages).
job_postings       Fetch job postings per company (default false).
job_postings_per_page  Max postings requested per company (default 25).
headcount_growth_min   e.g. 0.1 (= 10 %; values > 1 are read as percentages).
mapping            Overrides of the organization -> Company mapping
                   (canonical field -> Apollo field path).
default_signal     ``{type, title}`` attached to companies without any signal.
base_url, search_path, job_postings_path
                   Endpoint overrides (defaults: ``https://api.apollo.io``,
                   ``/api/v1/mixed_companies/search``,
                   ``/api/v1/organizations/{id}/job_postings``).
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from ..http import HttpError
from ..usage import BudgetExceeded
from ..models import Company, Signal, SignalType
from ..utils import get_path, parse_date, to_int
from .base import Source
from .mapping import (
    CompanyCollector,
    RecordMapper,
    as_text,
    clean_title,
    clean_url,
    is_blank,
    join_location,
    merge_mappings,
    parse_when,
)

DEFAULT_BASE_URL = "https://api.apollo.io"
SEARCH_PATH = "/api/v1/mixed_companies/search"
JOB_POSTINGS_PATH = "/api/v1/organizations/{id}/job_postings"
MAX_PER_PAGE = 100
OPEN_ENDED_MAX = 10_000_000

DEFAULT_MAPPING: Dict[str, Any] = {
    "name": "name",
    "website": "website_url",
    "domain": "primary_domain",
    "linkedin_url": "linkedin_url",
    "employees": ["estimated_num_employees", "num_employees"],
    "industry": ["industry", "industries.0"],
    "keywords": "keywords",
    "city": "city",
    "state": "state",
    "country": "country",
    "description": ["short_description", "seo_description"],
    "funding_stage": "latest_funding_stage",
    "funding_date": ["latest_funding_round_date", "_latest_funding_date"],
    "funding_amount": "_latest_funding_amount",
    "funding_currency": "_latest_funding_currency",
    "funding_total": "total_funding",
    "funding_url": "_latest_funding_url",
    "data.apollo_id": "_apollo_id",
    "data.phone": ["primary_phone.number", "phone", "sanitized_phone"],
    "data.founded_year": "founded_year",
    "data.total_funding": "total_funding",
    "data.headcount_growth_6m": "organization_headcount_six_month_growth",
    "data.headcount_growth_12m": "organization_headcount_twelve_month_growth",
    "data.revenue": ["organization_revenue", "annual_revenue"],
}


def employee_range(value: Any) -> Optional[str]:
    """Normalize one employee range to Apollo's ``"min,max"`` string."""
    if is_blank(value):
        return None
    lo: Optional[int]
    hi: Optional[int]
    if isinstance(value, dict):
        lo, hi = to_int(value.get("min")), to_int(value.get("max"))
    elif isinstance(value, (list, tuple)):
        if len(value) != 2:
            raise ValueError(f"employee range {value!r} must have exactly two numbers")
        lo, hi = to_int(value[0]), to_int(value[1])
    else:
        s = str(value).strip().replace(" ", "")
        if s.endswith("+"):
            lo, hi = to_int(s[:-1]), None
        else:
            sep = next((d for d in ("-", "–", ":") if d in s), None)
            if sep is None and s.count(",") == 1:
                sep = ","  # Apollo's own "11,50" form
            if sep is None:
                raise ValueError(f"employee range {value!r} must look like '11,50' or '11-50'")
            a, _, b = s.partition(sep)
            # with '-' as separator, commas are thousands separators ("1,001-5,000")
            lo, hi = to_int(a.replace(",", "")), to_int(b.replace(",", ""))
            if lo is None or hi is None:
                raise ValueError(f"employee range {value!r} must look like '11,50' or '11-50'")
    if lo is None and hi is None:
        return None
    lo = lo if lo is not None else 1
    hi = hi if hi is not None else OPEN_ENDED_MAX
    if lo > hi:
        lo, hi = hi, lo
    return f"{lo},{hi}"


def _is_single_range_pair(value: Any) -> bool:
    """[11, 50] / ["11", "50"]: two bare numbers form one range (each alone is not a range)."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return False
    for v in value:
        if isinstance(v, bool):
            return False
        if isinstance(v, (int, float)):
            continue
        if not (isinstance(v, str) and v.strip().isdigit()):  # "11,50" is itself a range
            return False
    return True


def _as_list(value: Any) -> List[Any]:
    if is_blank(value):
        return []
    if isinstance(value, (list, tuple)):
        return [v for v in value if not is_blank(v)]
    return [value]


def _stage_key(value: Any) -> str:
    """'Series B' / 'series_b' / 'SERIES-B' -> 'seriesb' (to compare funding round names)."""
    return "".join(ch for ch in as_text(value).lower() if ch.isalnum())


def _to_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class ApolloSource(Source):
    """Apollo organization search (see module docstring for config)."""

    name = "apollo"
    env_key = "APOLLO_API_KEY"

    # --- config ------------------------------------------------------------------
    @property
    def base_url(self) -> str:
        return str(self.config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")

    def _headers(self) -> Dict[str, str]:
        return {"x-api-key": self.secret(), "Content-Type": "application/json",
                "Cache-Control": "no-cache", "Accept": "application/json"}

    def _int(self, key: str, default: int) -> int:
        raw = self.config.get(key)
        if raw is None or raw == "":
            return default
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise ValueError(f"source {self.label}: '{key}' must be an integer, got {raw!r}") from None

    @property
    def per_page(self) -> int:
        default = min(self.limit, MAX_PER_PAGE) if self.limit else 25
        return max(1, min(MAX_PER_PAGE, self._int("per_page", default)))

    @property
    def max_pages(self) -> int:
        return max(1, self._int("max_pages", 2))

    @property
    def growth_min(self) -> Optional[float]:
        raw = self.config.get("headcount_growth_min")
        if is_blank(raw):
            return None
        val = _to_float(raw)
        if val is None:
            raise ValueError(f"source {self.label}: headcount_growth_min must be a number, got {raw!r}")
        return val / 100.0 if val > 1 else val

    def _locations(self) -> List[str]:
        locs = [str(x) for x in _as_list(self.config.get("locations"))]
        if not locs and self.config.get("use_icp", True):
            locs = [str(x) for x in _as_list(self.ctx.playbook.icp.get("locations"))]
        return locs

    def _employee_ranges(self) -> List[str]:
        raw = self.config.get("employee_ranges")
        ranges: List[str] = []
        try:
            if isinstance(raw, dict) or _is_single_range_pair(raw):
                raw = [raw]  # {min: 11, max: 50} / [11, 50] is one range, not two
            for r in _as_list(raw):
                rng = employee_range(r)
                if rng:
                    ranges.append(rng)
        except ValueError as e:
            raise ValueError(f"source {self.label}: {e}") from None
        if not ranges and raw in (None, "", []) and self.config.get("use_icp", True):
            emp = self.ctx.playbook.icp.get("employees") or {}
            rng = employee_range({"min": emp.get("min"), "max": emp.get("max")}) if isinstance(emp, dict) else None
            if rng:
                ranges.append(rng)
        return ranges

    def search_body(self, page: int) -> Dict[str, Any]:
        """Request body for one search page."""
        body: Dict[str, Any] = {}
        locations = self._locations()
        if locations:
            body["organization_locations"] = locations
        ranges = self._employee_ranges()
        if ranges:
            body["organization_num_employees_ranges"] = ranges
        keywords = [str(k) for k in _as_list(self.config.get("keywords"))]
        if keywords:
            body["q_organization_keyword_tags"] = keywords
        filters = self.config.get("filters") or {}
        if not isinstance(filters, dict):
            raise ValueError(f"source {self.label}: 'filters' must be a mapping of Apollo search fields")
        body.update(filters)
        body["page"] = page
        body["per_page"] = self.per_page
        return body

    # --- record preparation ----------------------------------------------------------
    @staticmethod
    def prepare(record: Dict[str, Any], kind: str = "organization") -> Dict[str, Any]:
        """Add derived keys used by the default mapping (apollo id, latest funding event, location)."""
        rec = dict(record)
        if kind == "account":
            rec["_apollo_id"] = rec.get("organization_id") or rec.get("id")
        else:
            rec["_apollo_id"] = rec.get("id") or rec.get("organization_id")
        events = [e for e in (rec.get("funding_events") or []) if isinstance(e, dict)]
        if events:
            target = parse_date(rec.get("latest_funding_round_date"))

            def when(e: Dict[str, Any]) -> date:
                return parse_date(e.get("date")) or date.min

            latest: Optional[Dict[str, Any]]
            if target:
                # only the event of the latest round itself: an older round's amount / news
                # URL must not be attached to the latest stage ("Series B ($5M)" + Series A link)
                latest = next((e for e in events if when(e) == target), None)
            else:
                latest = max(events, key=when)
                stage = rec.get("latest_funding_stage")
                if (latest is not None and not is_blank(stage) and not is_blank(latest.get("type"))
                        and _stage_key(stage) != _stage_key(latest.get("type"))):
                    latest = None  # the newest event is not the round named as the latest stage
            if latest is not None:
                rec["_latest_funding_amount"] = latest.get("amount")
                rec["_latest_funding_currency"] = latest.get("currency")
                rec["_latest_funding_url"] = latest.get("news_url")
                rec["_latest_funding_date"] = latest.get("date")
                if is_blank(rec.get("latest_funding_stage")) and latest.get("type"):
                    rec["latest_funding_stage"] = latest.get("type")
        return rec

    def growth_signal(self, record: Dict[str, Any]) -> Optional[Signal]:
        threshold = self.growth_min
        if threshold is None:
            return None
        six = _to_float(record.get("organization_headcount_six_month_growth"))
        twelve = _to_float(record.get("organization_headcount_twelve_month_growth"))
        for months, growth in ((6, six), (12, twelve)):
            if growth is not None and growth >= threshold:
                pct = round(growth * 100)
                return Signal(type=SignalType.HEADCOUNT_GROWTH,
                              title=f"Headcount up {pct}% in {months} months", source=self.label,
                              data={"six_month_growth": six, "twelve_month_growth": twelve,
                                    "employees": record.get("estimated_num_employees")})
        return None

    # --- API calls ---------------------------------------------------------------------
    def job_posting_signals(self, apollo_id: str, headers: Dict[str, str]) -> List[Signal]:
        path = str(self.config.get("job_postings_path") or JOB_POSTINGS_PATH)
        url = self.base_url + path.replace("{id}", str(apollo_id))
        params = {"page": 1, "per_page": max(1, self._int("job_postings_per_page", 25))}
        data = self.http.get_json(url, params=params, headers=headers) or {}
        postings = data.get("organization_job_postings") if isinstance(data, dict) else None
        if postings is None and isinstance(data, dict):
            postings = data.get("job_postings")
        out: List[Signal] = []
        for p in postings or []:
            if not isinstance(p, dict):
                continue
            title = clean_title(p.get("title"))
            if not title:
                continue
            out.append(Signal(
                type=SignalType.JOB_POSTING, title=title, source=self.label,
                posted_at=parse_when(p.get("posted_at"), self.ctx.today),
                url=clean_url(p.get("url")),
                location=join_location(p.get("city"), p.get("state"), p.get("country")),
                external_id=as_text(p.get("id")),
                data={k: p[k] for k in ("last_seen_at",) if not is_blank(p.get(k))},
            ))
        return out

    def fetch(self) -> List[Company]:
        if self.ctx.dry_run:
            self.log.info("dry-run: source %s (Apollo) skipped", self.label)
            return []
        headers = self._headers()
        mapper = RecordMapper(merge_mappings(DEFAULT_MAPPING, self.config.get("mapping")), label=self.label,
                              today=self.ctx.today, default_signal=self.config.get("default_signal"))
        url = self.base_url + str(self.config.get("search_path") or SEARCH_PATH)
        collector = CompanyCollector(self.limit)
        page = 0
        if not (self._locations() or self._employee_ranges() or self.config.get("keywords")
                or self.config.get("filters")):
            self.log.warning("source %s: no search filters set - Apollo returns arbitrary companies", self.label)
        while page < self.max_pages and not collector.full:
            page += 1
            try:
                data = self.http.post_json(url, json=self.search_body(page), headers=headers)
            except (HttpError, ValueError) as e:
                if page == 1:
                    raise
                self.log.warning("source %s: page %d failed (%s); keeping %d companies",
                                 self.label, page, e, len(collector))
                break
            except BudgetExceeded as e:
                if page == 1:
                    raise  # nothing was paid for yet
                self.budget_stop = str(e)
                self.log.warning("source %s: %s; keeping %d companies", self.label, e, len(collector))
                break
            if data is None:
                data = {}
            if not isinstance(data, dict):
                raise ValueError(f"source {self.label}: unexpected Apollo response ({type(data).__name__})")
            records: List[Tuple[str, Any]] = [("organization", r) for r in data.get("organizations") or []]
            records += [("account", r) for r in data.get("accounts") or []]
            if not records:
                break
            for kind, raw in records:
                if not isinstance(raw, dict):
                    continue
                rec = self.prepare(raw, kind)
                company = mapper.to_company(rec)
                if company is None:
                    continue
                growth = self.growth_signal(rec)
                if growth is not None:
                    company.signals.append(growth)
                collector.add(company)
            total_pages = to_int(get_path(data, "pagination.total_pages"))
            if total_pages is not None and page >= total_pages:
                break
        companies = collector.companies
        if self.config.get("job_postings"):
            for company in companies:
                apollo_id = company.data.get("apollo_id")
                if not apollo_id:
                    continue
                try:
                    signals = self.job_posting_signals(str(apollo_id), headers)
                except (HttpError, ValueError) as e:
                    self.log.warning("source %s: job postings for %s failed: %s", self.label, company.name, e)
                    continue
                except BudgetExceeded as e:  # keep every company (and its search signals) already paid for
                    self.budget_stop = str(e)
                    self.log.warning("source %s: %s; no more job-posting lookups", self.label, e)
                    break
                seen = {(s.fingerprint, s.external_id) for s in company.signals}
                for s in signals:
                    if (s.fingerprint, s.external_id) not in seen:
                        company.signals.append(s)
                        seen.add((s.fingerprint, s.external_id))
        self.log.info("source %s: %d companies from %d page(s)", self.label, len(companies), page)
        return companies


__all__ = ["ApolloSource", "DEFAULT_MAPPING", "employee_range"]
