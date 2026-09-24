"""Hunter.io domain search + email finder as a contact finder (``type: hunter``).

``find(company)`` - ``GET https://api.hunter.io/v2/domain-search`` with
``domain`` (or ``company=<name>`` when the company has no domain; neither ->
``[]``), ``api_key``, ``limit``, ``type=personal`` and optional
``seniority`` / ``department`` filters. Response (documented shape)::

    {"data": {"domain": "acme.com", "organization": "Acme", "pattern": "{first}.{last}",
              "accept_all": false, "webmail": false, "disposable": false,
              "emails": [{"value": "jane.doe@acme.com", "type": "personal",
                          "confidence": 94, "first_name": "Jane", "last_name": "Doe",
                          "position": "Chief Financial Officer", "seniority": "executive",
                          "department": "finance", "linkedin": "https://www.linkedin.com/in/janedoe",
                          "phone_number": null,
                          "verification": {"date": "2026-09-01", "status": "valid"}}]},
     "meta": {"results": 1, "limit": 10, "offset": 0, "params": {...}}}

Every email becomes a ``Contact`` (``source='hunter'``): ``confidence`` / 100
-> ``Contact.confidence``; ``verification.status`` valid -> valid, accept_all
-> risky, invalid -> invalid, anything else -> unknown (and unknown on an
accept-all domain -> risky). Hints left in ``company.data`` (nothing else on
the company is modified): ``email_pattern`` (Hunter's pattern, used first by
the ``pattern`` finder), ``email_accept_all`` (bool) and, when the company had
no domain, ``email_domain`` (the domain Hunter resolved from the name).

``complete(company, contact)`` - for a person without an email:
``GET https://api.hunter.io/v2/email-finder`` with ``domain`` (or
``company``), ``first_name``, ``last_name``, ``api_key`` -> ``{data: {email,
score, position, verification: {status}}}``; ``score`` / 100 ->
``confidence``. HTTP 400/404/422/451 (bad name, not found, person opted out)
leave the contact unchanged; other errors propagate.

Credential: ``HUNTER_API_KEY`` (or config ``api_key`` / ``api_key_env``), sent
as the ``api_key`` query parameter.

Config keys
-----------
limit                 Emails per domain search (default 10, max 100).
email_type            ``personal`` (default) or ``generic``; empty / ``all`` = both.
seniority             Hunter seniority filter: ``junior``, ``senior``,
                      ``executive`` (string, comma list or list).
department            Hunter department filter (string, comma list or list).
                      Hunter's set: executive, it, finance, management, sales,
                      legal, support, hr, marketing, communication, education,
                      design, health, operations. Common synonyms are mapped
                      (Human Resources / Talent -> hr, Engineering -> it,
                      C-Suite -> executive, Accounting -> finance, ...);
                      unknown values are dropped with a warning.
use_buyer_departments Default true: without ``department``, map
                      ``buyers.departments`` to Hunter's set. Hunter's
                      department is a hard filter, so ``executive`` (and
                      ``management`` for managing directors / general
                      managers) is added when ``buyers.titles`` holds
                      executive titles (Founder, CEO, Owner, Chief ...,
                      CFO, Managing Director, ...).
params                Extra raw query parameters for the domain search.
base_url              Default ``https://api.hunter.io/v2``.
domain_search_path    Default ``/domain-search``.
email_finder_path     Default ``/email-finder``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..http import HttpError, redact
from ..models import Company, Contact, EmailStatus
from ..utils import get_path, is_valid_email, normalize_domain, normalize_text
from .base import ContactFinder

DEFAULT_BASE_URL = "https://api.hunter.io/v2"
DOMAIN_SEARCH_PATH = "/domain-search"
EMAIL_FINDER_PATH = "/email-finder"
MAX_LIMIT = 100

HUNTER_DEPARTMENTS: Tuple[str, ...] = (
    "executive", "it", "finance", "management", "sales", "legal", "support", "hr", "marketing",
    "communication", "education", "design", "health", "operations",
)
HUNTER_SENIORITIES: Tuple[str, ...] = ("junior", "senior", "executive")

# (phrase, hunter department) - phrases are matched as whole words, in order.
DEPARTMENT_SYNONYMS: Tuple[Tuple[str, str], ...] = (
    ("c suite", "executive"), ("csuite", "executive"), ("c level", "executive"), ("executive", "executive"),
    ("executives", "executive"), ("leadership", "executive"), ("founder", "executive"),
    ("founders", "executive"), ("owner", "executive"), ("board", "executive"),
    ("information technology", "it"), ("it", "it"), ("engineering", "it"), ("technology", "it"),
    ("tech", "it"), ("software", "it"), ("development", "it"), ("devops", "it"), ("data", "it"),
    ("security", "it"), ("infrastructure", "it"),
    ("finance", "finance"), ("financial", "finance"), ("accounting", "finance"), ("accounts", "finance"),
    ("treasury", "finance"), ("tax", "finance"), ("audit", "finance"), ("payroll", "finance"),
    ("management", "management"), ("general management", "management"), ("administration", "management"),
    ("sales", "sales"), ("business development", "sales"), ("account management", "sales"),
    ("revenue", "sales"), ("commercial", "sales"),
    ("legal", "legal"), ("compliance", "legal"), ("law", "legal"), ("counsel", "legal"),
    ("support", "support"), ("customer service", "support"), ("customer success", "support"),
    ("helpdesk", "support"), ("service desk", "support"),
    ("hr", "hr"), ("human resources", "hr"), ("people operations", "hr"), ("people", "hr"),
    ("talent acquisition", "hr"), ("talent", "hr"),
    ("recruiting", "hr"), ("recruitment", "hr"), ("hiring", "hr"),
    ("marketing", "marketing"), ("growth", "marketing"), ("brand", "marketing"),
    ("demand generation", "marketing"), ("content", "marketing"),
    ("communication", "communication"), ("communications", "communication"),
    ("public relations", "communication"), ("pr", "communication"), ("media", "communication"),
    ("press", "communication"),
    ("education", "education"), ("training", "education"), ("learning", "education"),
    ("teaching", "education"),
    ("design", "design"), ("ux", "design"), ("ui", "design"), ("creative", "design"),
    ("health", "health"), ("healthcare", "health"), ("medical", "health"), ("clinical", "health"),
    ("nursing", "health"),
    ("operations", "operations"), ("ops", "operations"), ("supply chain", "operations"),
    ("logistics", "operations"), ("procurement", "operations"), ("facilities", "operations"),
    ("production", "operations"), ("manufacturing", "operations"),
)

# Buyer titles Hunter files under the "executive" / "management" departments (whole-word match).
EXECUTIVE_TITLE_PHRASES: Tuple[str, ...] = (
    "founder", "co founder", "cofounder", "owner", "ceo", "chief", "president", "managing director",
    "managing partner", "general manager", "c suite", "c level", "cfo", "coo", "cto", "cmo", "cro", "cio",
    "cpo", "chro", "ciso", "cco",
)
MANAGEMENT_TITLE_PHRASES: Tuple[str, ...] = ("managing director", "general manager", "managing partner")

_SYNONYMS_LONGEST_FIRST: Tuple[Tuple[str, str], ...] = tuple(
    sorted(DEPARTMENT_SYNONYMS, key=lambda t: -len(t[0].split())))

VERIFICATION_MAP: Dict[str, str] = {
    "valid": EmailStatus.VALID,
    "accept_all": EmailStatus.RISKY,
    "invalid": EmailStatus.INVALID,
}
# email-finder statuses that concern one person, not the account: leave the contact as it is.
SOFT_STATUSES = (400, 404, 422, 451)


def _as_list(value: Any) -> List[str]:
    if value in (None, "", [], ()):
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if v not in (None, "") and str(v).strip()]
    return [str(value).strip()]


def hunter_departments(value: Any) -> List[str]:
    """Map one free-text department ("Human Resources", "Sales & Marketing") to Hunter's set."""
    key = normalize_text(value)
    if not key:
        return []
    if key in HUNTER_DEPARTMENTS:
        return [key]
    # longest phrases first, and each match is consumed, so "Business Development" is sales
    # (not also "development" -> it) and "People Operations" is hr (not also operations).
    hay = f" {key} "
    out: List[str] = []
    for phrase, dept in _SYNONYMS_LONGEST_FIRST:
        needle = f" {phrase} "
        if needle in hay:
            hay = hay.replace(needle, " ")
            if dept not in out:
                out.append(dept)
    return out


def executive_departments(titles: Any) -> List[str]:
    """Hunter departments that hold the executive buyers among ``titles`` ([] when there are none)."""
    out: List[str] = []
    for title in _as_list(titles):
        hay = f" {normalize_text(title)} ".replace(" vice president ", " ")
        if any(f" {phrase} " in hay for phrase in EXECUTIVE_TITLE_PHRASES) and "executive" not in out:
            out.append("executive")
        if any(f" {phrase} " in hay for phrase in MANAGEMENT_TITLE_PHRASES) and "management" not in out:
            out.append("management")
    return out


def verification_status(raw: Any, accept_all: Any = None) -> str:
    status = VERIFICATION_MAP.get(normalize_text(raw).replace(" ", "_"), EmailStatus.UNKNOWN)
    if status == EmailStatus.UNKNOWN and accept_all is True:
        return EmailStatus.RISKY
    return status


def linkedin_url(value: Any) -> str:
    """Hunter returns a profile URL or (older payloads) a bare handle."""
    v = str(value or "").strip()
    if not v:
        return ""
    if v.lower().startswith(("http://", "https://")):
        return v
    if "linkedin.com" in v.lower():
        return "https://" + v.lstrip("/")
    return "https://www.linkedin.com/in/" + v.strip("/")


def _confidence(value: Any) -> Optional[float]:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return max(0.0, min(1.0, float(value) / 100.0))
    except (TypeError, ValueError):
        return None


class HunterFinder(ContactFinder):
    """Hunter domain search + email finder (see module docstring)."""

    name = "hunter"
    env_key = "HUNTER_API_KEY"
    offline = False

    # --- config helpers ----------------------------------------------------------------
    def _url(self, key: str, default: str) -> str:
        base = str(self.config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        return base + "/" + str(self.config.get(key) or default).lstrip("/")

    def _limit(self) -> int:
        try:
            n = int(self.config.get("limit") or 10)
        except (TypeError, ValueError):
            raise ValueError(f"hunter: limit must be an integer, got {self.config.get('limit')!r}") from None
        return max(1, min(MAX_LIMIT, n))

    def _departments(self) -> List[str]:
        if self.config.get("department"):
            raw = _as_list(self.config.get("department"))
        elif self.config.get("use_buyer_departments", True):
            raw = _as_list(self.ctx.playbook.buyers.get("departments"))
            if raw:
                # Hunter's department is a hard filter: keep founders / CEOs / owners (Hunter
                # department "executive") when they are among the buyer titles.
                raw.extend(executive_departments(self.ctx.playbook.buyers.get("titles")))
        else:
            raw = []
        out: List[str] = []
        for value in raw:
            mapped = hunter_departments(value)
            if not mapped:
                self.log.warning("hunter: department %r has no Hunter equivalent (%s); ignored",
                                 value, ", ".join(HUNTER_DEPARTMENTS))
            out.extend(d for d in mapped if d not in out)
        return out

    def _seniorities(self) -> List[str]:
        out: List[str] = []
        for value in _as_list(self.config.get("seniority")):
            v = normalize_text(value)
            if v in HUNTER_SENIORITIES:
                if v not in out:
                    out.append(v)
            else:
                self.log.warning("hunter: seniority %r is not one of %s; ignored", value,
                                 ", ".join(HUNTER_SENIORITIES))
        return out

    @staticmethod
    def _domain(company: Company) -> str:
        return company.domain or normalize_domain((company.data or {}).get("email_domain"))

    def _get_json(self, url: str, params: Dict[str, Any]) -> Any:
        try:
            return self.http.get_json(url, params=params)
        except HttpError as e:
            # network errors quote the request URL - api_key query parameter included
            body = redact(e.body or "")
            if body != (e.body or ""):
                raise HttpError(e.status, e.url, body) from None
            raise

    # --- domain search ------------------------------------------------------------------
    def search_params(self, company: Company) -> Optional[Dict[str, Any]]:
        """Query parameters for the domain search (None when the company cannot be searched)."""
        params: Dict[str, Any] = {}
        domain = self._domain(company)
        if domain:
            params["domain"] = domain
        elif company.name:
            params["company"] = company.name
        else:
            return None
        params["limit"] = self._limit()
        email_type = normalize_text(self.config.get("email_type", "personal"))
        if email_type and email_type != "all":
            params["type"] = email_type
        seniorities = self._seniorities()
        if seniorities:
            params["seniority"] = ",".join(seniorities)
        departments = self._departments()
        if departments:
            params["department"] = ",".join(departments)
        extra = self.config.get("params") or {}
        if not isinstance(extra, dict):
            raise ValueError("hunter: 'params' must be a mapping of query parameters")
        params.update(extra)
        return params

    def find(self, company: Company) -> List[Contact]:
        if self.ctx.dry_run:
            self.log.info("dry-run: hunter finder not called for %s", company.name)
            return []
        params = self.search_params(company)
        if params is None:
            return []
        params["api_key"] = self.secret()
        resp = self._get_json(self._url("domain_search_path", DOMAIN_SEARCH_PATH), params)
        data = get_path(resp, "data")
        if not isinstance(data, dict):
            raise ValueError(f"hunter: unexpected domain-search response for {company.name}: {resp!r:.200}")
        accept_all = data.get("accept_all")
        self._remember_hints(company, data)
        emails = data.get("emails") if isinstance(data.get("emails"), list) else []
        contacts = [c for c in (self.to_contact(e, accept_all) for e in emails) if c is not None]
        return contacts

    @staticmethod
    def _remember_hints(company: Company, data: Dict[str, Any]) -> None:
        pattern = data.get("pattern")
        if isinstance(pattern, str) and pattern.strip():
            company.data["email_pattern"] = pattern.strip()
        if isinstance(data.get("accept_all"), bool):
            company.data["email_accept_all"] = data["accept_all"]
        found_domain = normalize_domain(data.get("domain"))
        if not company.domain and found_domain:
            company.data.setdefault("email_domain", found_domain)

    def to_contact(self, item: Dict[str, Any], accept_all: Any = None) -> Optional[Contact]:
        """Map one domain-search ``emails[]`` entry to a Contact (None if unusable)."""
        if not isinstance(item, dict):
            return None
        email = str(item.get("value") or item.get("email") or "").strip().lower()
        if email and not is_valid_email(email):
            email = ""
        first = str(item.get("first_name") or "").strip()
        last = str(item.get("last_name") or "").strip()
        if not (email or first or last):
            return None
        raw_status = get_path(item, "verification.status")
        contact = Contact(
            first_name=first,
            last_name=last,
            title=str(item.get("position") or item.get("position_raw") or "").strip(),
            email=email,
            email_status=verification_status(raw_status, accept_all) if email else EmailStatus.UNKNOWN,
            linkedin_url=linkedin_url(item.get("linkedin") or item.get("linkedin_url")),
            phone=str(item.get("phone_number") or "").strip(),
            seniority=str(item.get("seniority") or "").strip(),
            department=str(item.get("department") or "").strip(),
            source=self.name,
            confidence=_confidence(item.get("confidence")),
        )
        if item.get("confidence") not in (None, ""):
            contact.data["hunter_confidence"] = item.get("confidence")
        if item.get("type"):
            contact.data["hunter_type"] = str(item["type"])
        if raw_status:
            contact.data["hunter_verification"] = str(raw_status)
        if email:
            contact.data["email_source"] = self.name
        return contact

    # --- email finder ----------------------------------------------------------------------
    def complete(self, company: Company, contact: Contact) -> Contact:
        if contact.email:
            return contact
        if self.ctx.dry_run:
            self.log.info("dry-run: hunter email-finder skipped for %s", contact.full_name or contact.key)
            return contact
        first = (contact.first_name or "").strip()
        last = (contact.last_name or "").strip()
        if not first or not last or "*" in last:
            return contact
        params: Dict[str, Any] = {}
        domain = self._domain(company)
        if domain:
            params["domain"] = domain
        elif company.name:
            params["company"] = company.name
        else:
            return contact
        params.update({"first_name": first, "last_name": last, "api_key": self.secret()})
        try:
            resp = self._get_json(self._url("email_finder_path", EMAIL_FINDER_PATH), params)
        except HttpError as e:
            if e.status in SOFT_STATUSES:
                self.log.info("hunter: no email for %s at %s (HTTP %d)", contact.full_name, company.name, e.status)
                return contact
            raise
        data = get_path(resp, "data")
        if not isinstance(data, dict):
            return contact
        email = str(data.get("email") or "").strip().lower()
        if not is_valid_email(email):
            return contact
        contact.email = email
        contact.email_status = verification_status(get_path(data, "verification.status"), data.get("accept_all"))
        score = _confidence(data.get("score"))
        if score is not None:
            contact.confidence = score
            contact.data["hunter_confidence"] = data.get("score")
        if not contact.title and data.get("position"):
            contact.title = str(data["position"]).strip()
        if not contact.linkedin_url:
            contact.linkedin_url = linkedin_url(data.get("linkedin_url") or data.get("linkedin"))
        if not contact.phone and data.get("phone_number"):
            contact.phone = str(data["phone_number"]).strip()
        if get_path(data, "verification.status"):
            contact.data["hunter_verification"] = str(get_path(data, "verification.status"))
        contact.data["email_source"] = self.name
        return contact


__all__ = ["DEPARTMENT_SYNONYMS", "EXECUTIVE_TITLE_PHRASES", "HUNTER_DEPARTMENTS", "HUNTER_SENIORITIES",
           "HunterFinder", "executive_departments", "hunter_departments", "linkedin_url",
           "verification_status"]
