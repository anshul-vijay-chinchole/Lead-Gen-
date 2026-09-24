"""Public ATS job boards (Greenhouse, Lever, Ashby) for a watchlist of companies.

Many companies publish their open roles through an applicant-tracking system
with a free, key-less JSON API. Give these sources a watchlist and every open
role becomes a ``job_posting`` signal on that company - a precise hiring
signal for accounts you already care about.

* Greenhouse: ``GET https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true``
  (``jobs[]``: ``id, title, first_published, updated_at, location.name,
  absolute_url, content`` - the content is HTML-escaped HTML);
* Lever: ``GET https://api.lever.co/v0/postings/{board}?mode=json``
  (``region: eu`` -> ``api.eu.lever.co``; ``[]``: ``id, text, createdAt`` (ms),
  ``hostedUrl, categories{location, team, commitment, department},
  descriptionPlain``);
* Ashby: ``GET https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=false``
  (``jobs[]``: ``id, title, location, publishedAt, jobUrl, isListed,
  department, team, descriptionPlain``; unlisted jobs are skipped).

One ``Company`` per watchlist entry that has at least one open job. A board
that answers 404 (wrong board name, company left the ATS) or fails otherwise
logs a warning and the others continue; only when *every* board fails is the
last error raised so the run reports it.

Config keys
-----------
companies    Watchlist: ``[{name, board, domain?, website?, location?, country?,
             industry?, employees?, linkedin_url?, region?}]``; a plain string is
             a board name. (A single ``board`` + ``name`` at the top level works too.)
             ``board`` is the company's slug on the ATS (e.g. the ``acme`` in
             ``boards.greenhouse.io/acme`` / ``jobs.lever.co/acme`` /
             ``jobs.ashbyhq.com/acme``).
limit        Max companies returned (0 = all).
max_jobs_per_company  Keep only the N freshest jobs per company (0 = all).
include_content       Greenhouse: request job descriptions (default true).
region       Lever: ``eu`` for EU-hosted accounts (per-entry ``region`` wins).
base_url     Override the API base (tests / proxies).
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from ..http import HttpError
from ..models import Company, Signal, SignalType
from .base import Source
from .mapping import (
    CompanyCollector,
    as_text,
    clean_description,
    clean_domain,
    clean_text,
    clean_title,
    clean_url,
    is_blank,
    parse_when,
    split_keywords,
)

MAX_JOB_LOCATIONS = 5


class _AtsSource(Source):
    """Shared watchlist loop; subclasses implement ``board_url`` + ``parse_jobs``."""

    ats = ""
    default_base_url = ""

    # --- subclass hooks -----------------------------------------------------------------
    def board_url(self, board: str, entry: Dict[str, Any]) -> str:  # pragma: no cover - abstract
        raise NotImplementedError

    def board_params(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        return {}

    def parse_jobs(self, body: Any) -> List[Dict[str, Any]]:  # pragma: no cover - abstract
        """Return normalized jobs: {id, title, url, date, location, description, department, team,
        commitment}."""
        raise NotImplementedError

    # --- shared ----------------------------------------------------------------------------
    def _base(self, default: str) -> str:
        return str(self.config.get("base_url") or default).rstrip("/")

    @staticmethod
    def _slug(board: str) -> str:
        return quote(board.strip(), safe="-_.")

    def entries(self) -> List[Dict[str, Any]]:
        """Normalized watchlist entries (entries without a board are skipped with a warning)."""
        raw = self.config.get("companies")
        if raw is None and self.config.get("board"):
            raw = [{k: v for k, v in self.config.items() if k not in ("type", "label", "companies", "limit")}]
        if raw is None:
            raise ValueError(f"source {self.label}: 'companies' (watchlist of {{name, board}}) is required")
        if isinstance(raw, (str, dict)):
            raw = [raw]
        if not isinstance(raw, list):
            raise ValueError(f"source {self.label}: 'companies' must be a list")
        out: List[Dict[str, Any]] = []
        for i, item in enumerate(raw):
            entry = {"board": item} if isinstance(item, str) else dict(item) if isinstance(item, dict) else {}
            board = as_text(entry.get("board")).strip()
            if not board:
                self.log.warning("source %s: companies[%d] has no 'board'; skipped", self.label, i)
                continue
            entry["board"] = board
            out.append(entry)
        return out

    def _company(self, entry: Dict[str, Any], jobs: List[Dict[str, Any]]) -> Company:
        board = entry["board"]
        website = clean_url(entry.get("website"))
        signals = [self._signal(j) for j in jobs]
        signals = [s for s in signals if s is not None]
        signals.sort(key=lambda s: s.posted_at or date.min, reverse=True)
        cap = int(self.config.get("max_jobs_per_company") or 0)
        if cap > 0:
            signals = signals[:cap]
        locations: List[str] = []
        for s in signals:
            if s.location and s.location not in locations:
                locations.append(s.location)
        location = clean_text(entry.get("location"), 300) or "; ".join(locations[:MAX_JOB_LOCATIONS])
        employees = entry.get("employees")
        company = Company(
            name=clean_text(entry.get("name"), 200) or board,
            domain=clean_domain(entry.get("domain")) or clean_domain(website),
            website=website,
            linkedin_url=clean_url(entry.get("linkedin_url")),
            location=location,
            country=clean_text(entry.get("country"), 100),
            industry=clean_text(entry.get("industry"), 200),
            employees=employees if not isinstance(employees, (bool, list, dict)) else None,
            description=clean_description(entry.get("description")),
            keywords=split_keywords(entry.get("keywords")),
            sources=[self.label],
            data={"ats": self.ats, "ats_board": board, "open_jobs": len(signals),
                  "job_locations": locations[:20]},
        )
        seen = set()
        for s in signals:
            key = (s.fingerprint, s.external_id)
            if key not in seen:
                seen.add(key)
                company.signals.append(s)
        return company

    def _signal(self, job: Dict[str, Any]) -> Optional[Signal]:
        title = clean_title(job.get("title"))
        if not title:
            return None
        data = {k: clean_text(job.get(k), 200) for k in ("department", "team", "commitment")
                if not is_blank(job.get(k)) and clean_text(job.get(k))}
        data["ats"] = self.ats
        return Signal(type=SignalType.JOB_POSTING, title=title, source=self.label,
                      posted_at=parse_when(job.get("date"), self.ctx.today), url=clean_url(job.get("url")),
                      location=clean_text(job.get("location"), 200),
                      description=clean_description(job.get("description")),
                      external_id=as_text(job.get("id")), data=data)

    def fetch(self) -> List[Company]:
        if self.ctx.dry_run:
            self.log.info("dry-run: source %s (%s boards) skipped", self.label, self.ats)
            return []
        entries = self.entries()
        collector = CompanyCollector(self.limit)
        attempted = failed = 0
        last_error: Optional[Exception] = None
        for entry in entries:
            if collector.full:
                break
            attempted += 1
            board = entry["board"]
            try:
                body = self.http.get_json(self.board_url(board, entry), params=self.board_params(entry))
            except HttpError as e:
                failed += 1
                last_error = e
                if e.status == 404:
                    self.log.warning("source %s: %s board %r not found (404); skipped", self.label, self.ats, board)
                else:
                    self.log.warning("source %s: %s board %r failed: %s", self.label, self.ats, board, e)
                continue
            except ValueError as e:  # 200 with a non-JSON body (maintenance page, ...)
                failed += 1
                last_error = ValueError(f"source {self.label}: {self.ats} board {board!r} did not return JSON: {e}")
                self.log.warning("%s", last_error)
                continue
            try:
                jobs = self.parse_jobs(body)
            except (TypeError, AttributeError, ValueError) as e:
                failed += 1
                self.log.warning("source %s: %s board %r returned an unexpected payload: %s",
                                 self.label, self.ats, board, e)
                continue
            if not jobs:
                self.log.debug("source %s: %s board %r has no open jobs", self.label, self.ats, board)
                continue
            collector.add(self._company(entry, jobs))
        if attempted and failed == attempted and last_error is not None:
            raise last_error
        self.log.info("source %s: %d of %d boards with open jobs", self.label, len(collector), attempted)
        return collector.companies


class GreenhouseSource(_AtsSource):
    """Greenhouse job boards (``boards-api.greenhouse.io``)."""

    name = "greenhouse"
    ats = "greenhouse"
    default_base_url = "https://boards-api.greenhouse.io/v1/boards"

    def board_url(self, board: str, entry: Dict[str, Any]) -> str:
        return f"{self._base(self.default_base_url)}/{self._slug(board)}/jobs"

    def board_params(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        return {"content": "true"} if self.config.get("include_content", True) else {}

    def parse_jobs(self, body: Any) -> List[Dict[str, Any]]:
        jobs = body.get("jobs") if isinstance(body, dict) else None
        out = []
        for j in jobs or []:
            if not isinstance(j, dict):
                continue
            depts = [as_text(d.get("name")) for d in j.get("departments") or [] if isinstance(d, dict)]
            loc = j.get("location")
            out.append({
                "id": j.get("id"), "title": j.get("title"), "url": j.get("absolute_url"),
                "date": j.get("first_published") or j.get("updated_at"),
                "location": loc.get("name") if isinstance(loc, dict) else loc,
                "description": j.get("content"),
                "department": ", ".join(d for d in depts if d),
            })
        return out


class LeverSource(_AtsSource):
    """Lever postings (``api.lever.co`` / ``api.eu.lever.co``)."""

    name = "lever"
    ats = "lever"
    default_base_url = "https://api.lever.co/v0/postings"
    eu_base_url = "https://api.eu.lever.co/v0/postings"

    def board_url(self, board: str, entry: Dict[str, Any]) -> str:
        region = str(entry.get("region") or self.config.get("region") or "").strip().lower()
        default = self.eu_base_url if region == "eu" else self.default_base_url
        base = str(self.config.get("base_url") or default).rstrip("/")
        return f"{base}/{self._slug(board)}"

    def board_params(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        return {"mode": "json"}

    def parse_jobs(self, body: Any) -> List[Dict[str, Any]]:
        if isinstance(body, dict):  # error envelope {"ok": false, "error": ...}
            if body.get("ok") is False:
                raise ValueError(str(body.get("error") or "Lever returned ok=false"))
            body = body.get("data") or []
        out = []
        for j in body or []:
            if not isinstance(j, dict):
                continue
            cats = j.get("categories") if isinstance(j.get("categories"), dict) else {}
            location = cats.get("location") or ", ".join(as_text(x) for x in cats.get("allLocations") or [])
            out.append({
                "id": j.get("id"), "title": j.get("text"), "url": j.get("hostedUrl") or j.get("applyUrl"),
                "date": j.get("createdAt"), "location": location,
                "description": j.get("descriptionPlain") or j.get("description"),
                "department": cats.get("department"), "team": cats.get("team"),
                "commitment": cats.get("commitment"),
            })
        return out


class AshbySource(_AtsSource):
    """Ashby job boards (``api.ashbyhq.com/posting-api``)."""

    name = "ashby"
    ats = "ashby"
    default_base_url = "https://api.ashbyhq.com/posting-api/job-board"

    def board_url(self, board: str, entry: Dict[str, Any]) -> str:
        return f"{self._base(self.default_base_url)}/{self._slug(board)}"

    def board_params(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        return {"includeCompensation": "false"}

    def parse_jobs(self, body: Any) -> List[Dict[str, Any]]:
        jobs = body.get("jobs") if isinstance(body, dict) else None
        out = []
        for j in jobs or []:
            if not isinstance(j, dict) or j.get("isListed") is False:
                continue
            location = j.get("location")
            if is_blank(location):
                address = (j.get("address") or {}).get("postalAddress") if isinstance(j.get("address"), dict) else None
                if isinstance(address, dict):
                    location = ", ".join(as_text(address.get(k)) for k in
                                         ("addressLocality", "addressRegion", "addressCountry") if address.get(k))
            out.append({
                "id": j.get("id"), "title": j.get("title"), "url": j.get("jobUrl") or j.get("applyUrl"),
                "date": j.get("publishedAt") or j.get("publishedDate"), "location": location,
                "description": j.get("descriptionPlain") or j.get("descriptionHtml"),
                "department": j.get("department"), "team": j.get("team"),
                "commitment": j.get("employmentType"),
            })
        return out


__all__ = ["GreenhouseSource", "LeverSource", "AshbySource"]
