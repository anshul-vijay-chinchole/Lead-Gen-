"""Apify source: run any Apify actor (scraper) - or read an existing dataset - and map its items.

Apify hosts thousands of scrapers (Google Maps, LinkedIn Jobs, Indeed, review
sites, ad libraries, ...). This adapter runs one and maps its dataset items
to companies with the shared mapping machinery (``leadgen.sources.mapping``),
so any actor can be used as a signal source by writing a ``mapping``.

Credential: ``APIFY_TOKEN`` (or config ``api_key`` / ``api_key_env``), sent as
the ``token`` query parameter (``HttpClient`` redacts it from logs).

Modes (first one configured wins):
  * ``dataset_id`` - GET ``/v2/datasets/{id}/items`` (read a finished run, free);
  * ``actor`` + ``use_last_run: true`` - GET
    ``/v2/acts/{actor}/runs/last/dataset/items`` (items of the actor's last
    successful run, e.g. one scheduled in the Apify console);
  * ``actor`` - POST ``/v2/acts/{actor}/run-sync-get-dataset-items`` with the
    actor ``input`` as JSON body; waits for the run (Apify caps synchronous
    runs at 300 s, longer runs answer HTTP 408 - use a schedule + ``dataset_id``
    / ``use_last_run`` for those).

Config keys
-----------
actor            Actor id, e.g. ``compass/crawler-google-places`` ('/' becomes '~').
input            Actor input (dict), passed through untouched.
dataset_id       Read this dataset instead of running an actor.
use_last_run     With ``actor``: read the last successful run's dataset.
preset           ``google_maps`` | ``linkedin_jobs`` | ``indeed_jobs`` | ``none``.
                 Default: guessed from the actor name, else none. A preset
                 supplies a best-effort mapping, signal type and title template.
mapping          Field overrides (canonical field -> item key / dotted path);
                 they win over the preset. Without a preset, flat item keys are
                 auto-detected like CSV headers.
defaults         Field -> constant for items without a value.
signal_type      Type of the per-item signal (default: preset's, else
                 ``job_posting`` for job-like items, else ``custom``).
signal_title_template  e.g. ``"Rated {totalScore} with {reviewsCount} reviews"``
                 (``null`` disables the preset's template).
signal_requires  Item paths that must be non-empty for an item's own signal
                 (google_maps preset: ``["totalScore"]``; ``[]`` disables).
default_signal   ``{type, title, ...}`` for items without a signal of their own.
people           ``{path, mapping}`` list of people inside each item.
limit            Max companies returned (0 = all).
max_items        Max dataset items to download (Apify ``limit`` parameter).
timeout_secs     Actor run timeout (default 300); the HTTP timeout is 30 s more.
memory_mbytes    Actor run memory (optional).
base_url         API base, default ``https://api.apify.com/v2``.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from ..models import Company, SignalType
from .base import Source
from .mapping import (
    RecordMapper,
    detect_mapping,
    merge_mappings,
    normalize_defaults,
    record_keys,
    records_to_companies,
)

DEFAULT_BASE_URL = "https://api.apify.com/v2"
DEFAULT_TIMEOUT_SECS = 300

PRESETS: Dict[str, Dict[str, Any]] = {
    # compass/crawler-google-places and compatible Google Maps scrapers
    "google_maps": {
        "signal_type": SignalType.REVIEW,
        "signal_title_template": "Rated {totalScore} from {reviewsCount} Google reviews",
        "signal_requires": ["totalScore"],
        "mapping": {
            "name": ["title", "name"],
            "website": ["website", "url_website"],
            "location": ["address", "fullAddress"],
            "city": "city",
            "state": "state",
            "country": ["countryCode", "country"],
            "industry": ["categoryName", "category"],
            "keywords": "categories",
            "description": "description",
            "email": ["emails.0", "email"],
            "phone": ["phone", "phoneUnformatted"],
            "signal_url": "url",
            "signal_id": ["placeId", "cid"],
            "signal_location": "address",
            "signal_data.rating": "totalScore",
            "signal_data.reviews_count": "reviewsCount",
            "data.phone": ["phone", "phoneUnformatted"],
            "data.rating": "totalScore",
            "data.reviews_count": "reviewsCount",
            "data.google_maps_url": "url",
            "data.place_id": "placeId",
            "data.permanently_closed": "permanentlyClosed",
            "data.temporarily_closed": "temporarilyClosed",
        },
    },
    # bebity/linkedin-jobs-scraper, curious_coder/linkedin-jobs-scraper and similar
    "linkedin_jobs": {
        "signal_type": SignalType.JOB_POSTING,
        "mapping": {
            "name": ["companyName", "company", "company_name"],
            "website": ["companyWebsite", "companyUrl"],
            "linkedin_url": ["companyLinkedinUrl", "companyLinkedInUrl", "companyUrl"],
            "industry": ["industries", "companyIndustry", "sector"],
            "employees": ["companyEmployeesCount", "companySize", "employeeCount"],
            "description": ["companyDescription"],
            "signal_title": ["title", "jobTitle", "positionName"],
            "signal_url": ["link", "jobUrl", "url", "applyUrl"],
            "signal_date": ["postedAt", "publishedAt", "postedTime", "postedDate", "listedAt"],
            "signal_id": ["id", "jobId"],
            "signal_location": ["location", "place", "jobLocation"],
            "signal_description": ["descriptionText", "description", "jobDescription"],
            "signal_data.seniority": ["seniorityLevel", "experienceLevel"],
            "signal_data.employment_type": ["employmentType", "contractType"],
            "signal_data.applicants": ["applicantsCount", "applicationsCount"],
            "signal_data.salary": ["salary", "salaryInfo"],
            "full_name": ["jobPosterName", "posterFullName", "poster.name"],
            "title": ["jobPosterTitle", "posterTitle", "poster.title"],
            "person_linkedin_url": ["jobPosterProfileUrl", "posterProfileUrl", "poster.profileUrl"],
        },
    },
    # misceres/indeed-scraper and similar
    "indeed_jobs": {
        "signal_type": SignalType.JOB_POSTING,
        "mapping": {
            "name": ["company", "companyName"],
            "website": ["companyInfo.companyWebsite", "companyWebsite"],
            "description": ["companyInfo.companyDescription"],
            "signal_title": ["positionName", "title"],
            "signal_url": ["url", "externalApplyLink", "jobUrl"],
            "signal_date": ["postingDateParsed", "postedAt", "postingDate", "datePosted"],
            "signal_id": ["id", "jobKey"],
            "signal_location": ["location", "jobLocation"],
            "signal_description": ["description", "descriptionText"],
            "signal_data.salary": "salary",
            "signal_data.job_type": "jobType",
            "data.indeed_company_url": ["companyInfo.indeedUrl"],
            "data.rating": ["rating", "companyInfo.rating"],
        },
    },
}

_PRESET_HINTS = (
    ("google_maps", ("google-places", "google-maps", "googlemaps", "google_maps", "crawler-google")),
    ("linkedin_jobs", ("linkedin-jobs", "linkedin-job", "linkedinjobs")),
    ("indeed_jobs", ("indeed",)),
)


def guess_preset(actor: str) -> str:
    a = (actor or "").lower()
    for preset, hints in _PRESET_HINTS:
        if any(h in a for h in hints):
            return preset
    return ""


class ApifySource(Source):
    """Run an Apify actor / read a dataset and map the items (see module docstring)."""

    name = "apify"
    env_key = "APIFY_TOKEN"

    @property
    def base_url(self) -> str:
        return str(self.config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")

    @property
    def preset(self) -> str:
        raw = self.config.get("preset")
        if raw is None:
            return guess_preset(str(self.config.get("actor") or ""))
        p = str(raw).strip().lower()
        if p in ("", "none", "false", "off"):
            return ""
        if p not in PRESETS:
            raise ValueError(f"source {self.label}: unknown preset {raw!r}; expected one of "
                             f"{', '.join(PRESETS)} or none")
        return p

    @staticmethod
    def actor_path(actor: str) -> str:
        return quote(str(actor).strip().replace("/", "~"), safe="~._-")

    def _timeout(self) -> int:
        try:
            return max(1, int(self.config.get("timeout_secs") or DEFAULT_TIMEOUT_SECS))
        except (TypeError, ValueError):
            raise ValueError(f"source {self.label}: timeout_secs must be an integer") from None

    def _item_params(self, token: str) -> Dict[str, Any]:
        params: Dict[str, Any] = {"token": token, "format": "json", "clean": "true"}
        if self.config.get("max_items"):
            params["limit"] = int(self.config["max_items"])
        return params

    def fetch_items(self) -> List[Dict[str, Any]]:
        """Download the dataset items (one HTTP call)."""
        actor = self.config.get("actor")
        dataset_id = self.config.get("dataset_id")
        if not actor and not dataset_id:
            raise ValueError(f"source {self.label}: set 'actor' (to run a scraper) or 'dataset_id'")
        token = self.secret()
        if dataset_id:
            url = f"{self.base_url}/datasets/{quote(str(dataset_id), safe='~._-')}/items"
            resp = self.http.get(url, params=self._item_params(token))
        elif self.config.get("use_last_run"):
            url = f"{self.base_url}/acts/{self.actor_path(actor)}/runs/last/dataset/items"
            params = self._item_params(token)
            params["status"] = "SUCCEEDED"
            resp = self.http.get(url, params=params)
        else:
            timeout = self._timeout()
            params = self._item_params(token)
            params["timeout"] = timeout
            if self.config.get("memory_mbytes"):
                params["memory"] = int(self.config["memory_mbytes"])
            actor_input = self.config.get("input") or {}
            if not isinstance(actor_input, dict):
                raise ValueError(f"source {self.label}: 'input' must be a mapping (the actor input JSON)")
            url = f"{self.base_url}/acts/{self.actor_path(actor)}/run-sync-get-dataset-items"
            self.log.info("source %s: running Apify actor %s (up to %ds)", self.label, actor, timeout)
            resp = self.http.post(url, params=params, json=copy.deepcopy(actor_input), timeout=timeout + 30)
        return self._parse_items(resp.json())

    def _parse_items(self, body: Any) -> List[Dict[str, Any]]:
        if body is None:
            return []
        if isinstance(body, dict):
            err = body.get("error")
            if err:
                msg = err.get("message") if isinstance(err, dict) else err
                raise ValueError(f"source {self.label}: Apify error: {msg}")
            for key in ("items", "data"):
                if isinstance(body.get(key), list):
                    body = body[key]
                    break
            else:
                body = [body]
        if not isinstance(body, list):
            raise ValueError(f"source {self.label}: unexpected Apify response ({type(body).__name__})")
        return [it for it in body if isinstance(it, dict)]

    def signal_requires(self) -> List[str]:
        if "signal_requires" in self.config:
            return list(self.config.get("signal_requires") or [])
        preset = PRESETS.get(self.preset) if self.preset else None
        return list((preset or {}).get("signal_requires") or [])

    def build_mapping(self, items: List[Dict[str, Any]]) -> Tuple[Dict[str, List[str]], Dict[str, Any], Optional[str]]:
        """Return (mapping, defaults, signal_title_template) for these items."""
        preset = PRESETS.get(self.preset) if self.preset else None
        if preset:
            base = preset["mapping"]
            auto_type = preset.get("signal_type")
            template = preset.get("signal_title_template")
        else:
            base, info = detect_mapping(record_keys(items))
            auto_type = info.get("signal_type")
            template = None
        if "signal_title_template" in self.config:
            template = self.config.get("signal_title_template") or None
        mapping = merge_mappings(base, self.config.get("mapping"))
        defaults = normalize_defaults(self.config.get("defaults"))
        defaults["signal_type"] = (self.config.get("signal_type") or defaults.get("signal_type")
                                   or auto_type or SignalType.CUSTOM)
        return mapping, defaults, template

    def validate(self) -> None:
        """Fail fast on config errors *before* paying for an actor run."""
        _ = self.preset
        merge_mappings(self.config.get("mapping"))
        RecordMapper({}, label=self.label, signal_title_template=self.config.get("signal_title_template"),
                     default_signal=self.config.get("default_signal"), people=self.config.get("people"),
                     defaults=self.config.get("defaults"))

    def fetch(self) -> List[Company]:
        if self.ctx.dry_run:
            self.log.info("dry-run: source %s (Apify) skipped", self.label)
            return []
        self.validate()
        items = self.fetch_items()
        if not items:
            self.log.warning("source %s: Apify returned no items", self.label)
            return []
        mapping, defaults, template = self.build_mapping(items)
        companies = records_to_companies(
            items, mapping, label=self.label, defaults=defaults, today=self.ctx.today, limit=self.limit,
            signal_title_template=template, default_signal=self.config.get("default_signal"),
            people=self.config.get("people"),
            location_from_signal=bool(self.config.get("location_from_signal", True)),
            signal_requires=self.signal_requires(), log=self.log,
        )
        self.log.info("source %s: %d items -> %d companies", self.label, len(items), len(companies))
        return companies


__all__ = ["ApifySource", "PRESETS", "guess_preset"]
