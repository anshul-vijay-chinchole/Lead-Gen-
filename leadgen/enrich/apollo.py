"""Apollo.io people search + email reveal as a contact finder (``type: apollo``).

``find(company)``

1. ``POST {base_url}{search_path}`` with the company - its Apollo organization
   id (``company.data['apollo_id']``, left there by the Apollo source) as
   ``organization_ids``, else its domain as ``q_organization_domains_list``;
   a company with neither is skipped (``[]``) - plus the buyer hints from the
   playbook: ``person_titles`` = ``buyers.titles`` (priority order),
   ``person_seniorities`` = ``buyers.seniorities`` (Apollo values such as
   ``owner, founder, c_suite, partner, vp, head, director, manager``),
   ``include_similar_titles: true``, ``page: 1``, ``per_page``.
   People are read from ``people`` and (saved CRM records) ``contacts``.
2. Reveal: Apollo's search usually returns people without a usable email
   (none, or a placeholder such as ``email_not_unlocked@domain.com``). For
   the best of them - excluded titles (``buyers.exclude_titles``) skipped,
   ordered by the first ``buyers.titles`` entry their title contains, then
   Apollo's order - ``POST /api/v1/people/match`` with ``{id,
   reveal_personal_emails: false}`` (one credit each) until
   ``buyers.max_contacts_per_company`` good people have an email or
   ``reveal_limit`` calls were made. A reveal failure never loses the people
   already found: it is logged and revealing stops (after HTTP 401/402/403 -
   e.g. a plan without enrichment access - no further match calls are made
   by this finder instance).

``complete(company, contact)`` reveals one person lacking an email via
``people/match`` - by Apollo id (``contact.data['apollo_id']``) when known,
else by ``first_name`` + ``last_name`` + ``domain`` (``organization_name``
when the company has no domain; ``linkedin_url`` added when known), else by
LinkedIn URL alone. People already tried, or flagged by Apollo as having no
email (``has_email: false``), are not looked up again; a company never costs
more than ``complete_limit`` people/match calls through ``complete`` - the
reveals ``find`` made for it count toward that budget; HTTP 404/422 (no such
person) leave the contact unchanged; HTTP 401/402/403 block people/match for
the rest of the run (like a failed reveal) without stopping the search.

Mapping: ``email_status`` verified -> valid; catch_all / accept_all -> risky;
bounced / invalid -> invalid; likely to engage / extrapolated / guessed /
unavailable / unverified / anything else -> unknown. Obfuscated last names
(``last_name_obfuscated``, e.g. ``"Sm***h"``, returned by the new search
endpoint) are never used as a real name: the contact keeps the first name with
the obfuscated form in ``full_name`` until a reveal supplies the real name.
The Apollo person id is kept in ``contact.data['apollo_id']``. When the search
ran by domain and every person shares one organization id, it is saved to
``company.data['apollo_id']`` (a hint for later lookups; nothing else on the
company is touched).

Credential: ``APOLLO_API_KEY`` (or config ``api_key`` / ``api_key_env``), sent
in the ``x-api-key`` header.

Config keys
-----------
per_page                Page size (default 10, max 100). Only page 1 is read.
titles                  Override ``buyers.titles`` for the search.
seniorities             Override ``buyers.seniorities`` for the search.
include_similar_titles  Default true.
filters                 Raw request-body fields merged into the search as-is
                        (any Apollo people-search filter).
reveal_emails           Default true. False disables the reveal step *and*
                        ``complete`` (no enrichment credits are spent).
reveal_limit            Max ``people/match`` calls per ``find`` (default 2; 0 = none).
complete_limit          Max ``people/match`` calls per company once ``complete``
                        runs, the reveals ``find`` made for the company included
                        (default 3; 0 = ``complete`` makes none) - a credit guard
                        for companies with many email-less people. A company
                        costs at most max(reveal_limit, complete_limit) credits.
reveal_require_title_match
                        Only reveal people whose title contains a buyer title
                        (default false: others are revealed after them).
reveal_personal_emails  Sent to ``people/match`` (default false).
base_url                Default ``https://api.apollo.io``.
search_path             Default ``/api/v1/mixed_people/api_search`` (the legacy
                        endpoint is ``/api/v1/mixed_people/search``).
match_path              Default ``/api/v1/people/match``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..http import HttpError
from ..usage import BudgetExceeded
from ..models import Company, Contact, EmailStatus
from ..utils import contains_any, is_valid_email, normalize_text
from .base import ContactFinder

DEFAULT_BASE_URL = "https://api.apollo.io"
SEARCH_PATH = "/api/v1/mixed_people/api_search"
LEGACY_SEARCH_PATH = "/api/v1/mixed_people/search"
MATCH_PATH = "/api/v1/people/match"
MAX_PER_PAGE = 100

EMAIL_STATUS_MAP: Dict[str, str] = {
    "verified": EmailStatus.VALID,
    "valid": EmailStatus.VALID,
    "catch all": EmailStatus.RISKY,
    "catchall": EmailStatus.RISKY,
    "accept all": EmailStatus.RISKY,
    "bounced": EmailStatus.INVALID,
    "invalid": EmailStatus.INVALID,
    "likely to engage": EmailStatus.UNKNOWN,
    "extrapolated": EmailStatus.UNKNOWN,
    "guessed": EmailStatus.UNKNOWN,
    "unavailable": EmailStatus.UNKNOWN,
    "unverified": EmailStatus.UNKNOWN,
}
# HTTP statuses after which people/match is not worth calling again this run.
BLOCKING_STATUSES = (401, 402, 403)
# HTTP statuses that just mean "no such person" for people/match.
NOT_FOUND_STATUSES = (404, 422)
_PLACEHOLDER_LOCALS = ("email_not_unlocked", "email_not_available", "not_unlocked", "no_email")
_PLACEHOLDER_DOMAINS = frozenset({"domain.com", "example.com"})


def apollo_email_status(raw: Any) -> str:
    """Apollo ``email_status`` -> ``EmailStatus`` (unknown for anything unexpected)."""
    return EMAIL_STATUS_MAP.get(normalize_text(raw), EmailStatus.UNKNOWN)


def clean_email(raw: Any) -> str:
    """The address, or '' for empty / malformed / placeholder values."""
    email = str(raw or "").strip().lower()
    if not is_valid_email(email):
        return ""
    local, _, domain = email.partition("@")
    if local.startswith(_PLACEHOLDER_LOCALS) or domain in _PLACEHOLDER_DOMAINS:
        return ""
    return email


def _clean_name(raw: Any) -> str:
    s = str(raw or "").strip()
    return "" if "*" in s else s


def _as_str_list(value: Any) -> List[str]:
    if value in (None, "", [], ()):
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if v not in (None, "") and str(v).strip()]
    return [str(value)]


def _department(person: Dict[str, Any]) -> str:
    for key in ("departments", "subdepartments"):
        values = _as_str_list(person.get(key))
        if values:
            dep = values[0]
            if dep.startswith("master_"):
                dep = dep[len("master_"):]
            return dep.replace("_", " ").strip()
    return ""


def _phone(person: Dict[str, Any]) -> str:
    numbers = person.get("phone_numbers")
    if isinstance(numbers, list):
        for n in numbers:
            if isinstance(n, dict):
                value = n.get("sanitized_number") or n.get("raw_number")
                if value:
                    return str(value)
    for key in ("sanitized_phone", "phone", "mobile_phone", "direct_phone"):
        if person.get(key):
            return str(person[key])
    return ""


def _location(person: Dict[str, Any]) -> str:
    parts: List[str] = []
    for key in ("city", "state", "country"):
        v = str(person.get(key) or "").strip()
        if v and v not in parts:
            parts.append(v)
    return ", ".join(parts) or str(person.get("location") or person.get("present_raw_address") or "").strip()


def _organization_id(person: Dict[str, Any]) -> str:
    org = person.get("organization")
    org_id = person.get("organization_id") or (org.get("id") if isinstance(org, dict) else None)
    return str(org_id or "").strip()


def _confidence(person: Dict[str, Any]) -> Optional[float]:
    raw = person.get("extrapolated_email_confidence")
    if raw in (None, "") or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value > 1:
        value /= 100.0
    return max(0.0, min(1.0, value))


class ApolloFinder(ContactFinder):
    """Apollo people search + email reveal (see module docstring)."""

    name = "apollo"
    env_key = "APOLLO_API_KEY"
    offline = False

    def __init__(self, config: Dict[str, Any], ctx: Any):
        super().__init__(config, ctx)
        self._match_blocked = ""
        self._completed: Dict[str, int] = {}  # company key -> people/match calls (find reveals + complete)

    # --- config helpers -------------------------------------------------------------
    def _url(self, key: str, default: str) -> str:
        base = str(self.config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        return base + "/" + str(self.config.get(key) or default).lstrip("/")

    def _headers(self) -> Dict[str, str]:
        return {"x-api-key": self.secret(), "Content-Type": "application/json",
                "Accept": "application/json", "Cache-Control": "no-cache"}

    def _per_page(self) -> int:
        try:
            n = int(self.config.get("per_page") or 10)
        except (TypeError, ValueError):
            raise ValueError(f"apollo: per_page must be an integer, got {self.config.get('per_page')!r}") from None
        return max(1, min(MAX_PER_PAGE, n))

    def _titles(self) -> List[str]:
        if self.config.get("titles"):
            return _as_str_list(self.config.get("titles"))
        return _as_str_list(self.ctx.playbook.buyers.get("titles"))

    def _seniorities(self) -> List[str]:
        if self.config.get("seniorities"):
            return _as_str_list(self.config.get("seniorities"))
        return _as_str_list(self.ctx.playbook.buyers.get("seniorities"))

    def _reveal_enabled(self) -> bool:
        return bool(self.config.get("reveal_emails", True))

    def _reveal_limit(self) -> int:
        try:
            return max(0, int(self.config.get("reveal_limit", 2) or 0))
        except (TypeError, ValueError):
            raise ValueError(f"apollo: reveal_limit must be an integer, "
                             f"got {self.config.get('reveal_limit')!r}") from None

    def _complete_limit(self) -> int:
        try:
            return max(0, int(self.config.get("complete_limit", 3) or 0))
        except (TypeError, ValueError):
            raise ValueError(f"apollo: complete_limit must be an integer, "
                             f"got {self.config.get('complete_limit')!r}") from None

    def title_rank(self, title: str) -> Optional[int]:
        """Index of the first buyer title contained in ``title`` (None = no match)."""
        if not title:
            return None
        for i, wanted in enumerate(self._titles()):
            if contains_any(title, [wanted]):
                return i
        return None

    def is_excluded(self, title: str) -> bool:
        """True if ``title`` contains one of ``buyers.exclude_titles`` (intern, assistant, ...)."""
        if not title:
            return False
        excluded = _as_str_list(self.ctx.playbook.buyers.get("exclude_titles"))
        return contains_any(title, excluded) is not None

    # --- search ----------------------------------------------------------------------
    def search_body(self, company: Company) -> Optional[Dict[str, Any]]:
        """The people-search request body for ``company`` (None when it cannot be searched)."""
        body: Dict[str, Any] = {"page": 1, "per_page": self._per_page()}
        apollo_id = str((company.data or {}).get("apollo_id") or "").strip()
        if apollo_id:
            body["organization_ids"] = [apollo_id]
        elif company.domain:
            body["q_organization_domains_list"] = [company.domain]
        else:
            return None
        titles = self._titles()
        if titles:
            body["person_titles"] = titles
            body["include_similar_titles"] = bool(self.config.get("include_similar_titles", True))
        seniorities = self._seniorities()
        if seniorities:
            body["person_seniorities"] = seniorities
        filters = self.config.get("filters") or {}
        if not isinstance(filters, dict):
            raise ValueError("apollo: 'filters' must be a mapping of Apollo search fields")
        body.update(filters)
        return body

    def find(self, company: Company) -> List[Contact]:
        if self.ctx.dry_run:
            self.log.info("dry-run: apollo finder not called for %s", company.name)
            return []
        body = self.search_body(company)
        if body is None:
            self.log.debug("apollo: %s has no domain or Apollo id; skipped", company.name)
            return []
        resp = self.http.post_json(self._url("search_path", SEARCH_PATH), json=body, headers=self._headers())
        if not isinstance(resp, dict):
            raise ValueError(f"apollo: unexpected people-search response for {company.name}: {resp!r:.200}")
        people = self._people(resp)
        contacts = [c for c in (self.to_contact(p) for p in people) if c is not None]
        self._remember_org(company, people, by_domain="organization_ids" not in body)
        if contacts and self._reveal_enabled():
            self._reveal(contacts, company)
        return contacts

    @staticmethod
    def _people(resp: Dict[str, Any]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        seen = set()
        for key in ("people", "contacts"):
            items = resp.get(key)
            if not isinstance(items, list):
                continue
            for p in items:
                if not isinstance(p, dict):
                    continue
                pid = str(p.get("person_id") or p.get("id") or "") if key == "contacts" else str(p.get("id") or "")
                if pid and pid in seen:
                    continue
                if pid:
                    seen.add(pid)
                out.append(dict(p, _apollo_person_id=pid))
        return out

    @staticmethod
    def _remember_org(company: Company, people: List[Dict[str, Any]], by_domain: bool) -> None:
        if not by_domain or (company.data or {}).get("apollo_id"):
            return
        org_ids = {_organization_id(p) for p in people}
        org_ids.discard("")
        if len(org_ids) == 1:
            company.data["apollo_id"] = org_ids.pop()

    def to_contact(self, person: Dict[str, Any]) -> Optional[Contact]:
        """Map one Apollo person (search or match result) to a Contact (None if unusable)."""
        if not isinstance(person, dict):
            return None
        pid = str(person.get("_apollo_person_id") or person.get("id") or "")
        first = _clean_name(person.get("first_name"))
        last = _clean_name(person.get("last_name"))
        obfuscated = str(person.get("last_name_obfuscated") or "").strip()
        if not obfuscated and "*" in str(person.get("last_name") or ""):
            obfuscated = str(person.get("last_name")).strip()
        name = _clean_name(person.get("name"))
        if not name:
            name = " ".join(x for x in (first, last or obfuscated) if x)
        email = clean_email(person.get("email"))
        linkedin = str(person.get("linkedin_url") or "").strip()
        if not (first or last or name or email or linkedin):
            return None
        raw_status = person.get("email_status")
        contact = Contact(
            first_name=first,
            last_name=last,
            full_name=name,
            title=str(person.get("title") or person.get("headline") or "").strip(),
            email=email,
            email_status=apollo_email_status(raw_status) if email else EmailStatus.UNKNOWN,
            linkedin_url=linkedin,
            phone=_phone(person),
            seniority=str(person.get("seniority") or "").strip(),
            department=_department(person),
            location=_location(person),
            source=self.name,
            confidence=_confidence(person),
        )
        if pid:
            contact.data["apollo_id"] = pid
        if raw_status:
            contact.data["apollo_email_status"] = str(raw_status)
        if isinstance(person.get("has_email"), bool):
            contact.data["apollo_has_email"] = person["has_email"]
        if obfuscated and not last:
            contact.data["apollo_last_name_obfuscated"] = obfuscated
        org_id = _organization_id(person)
        if org_id:
            contact.data["apollo_organization_id"] = org_id
        if email:
            contact.data["email_source"] = self.name
        return contact

    # --- reveal (people/match) --------------------------------------------------------------
    def _match(self, body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            resp = self.http.post_json(self._url("match_path", MATCH_PATH), json=body, headers=self._headers())
        except ValueError as e:  # a 2xx whose body is not JSON (proxy / CDN / maintenance page)
            raise ValueError(f"apollo: people/match response is not JSON ({e})") from e
        if not isinstance(resp, dict):
            return None
        person = resp.get("person")
        return person if isinstance(person, dict) else None

    def _revealable(self, contact: Contact) -> bool:
        return (not contact.email and bool(contact.data.get("apollo_id"))
                and not contact.data.get("apollo_revealed")
                and contact.data.get("apollo_has_email") is not False)

    def _reveal(self, contacts: List[Contact], company: Optional[Company] = None) -> None:
        limit = self._reveal_limit()
        if limit <= 0 or self._match_blocked:
            return
        needed = max(1, int(self.ctx.playbook.buyers.get("max_contacts_per_company") or 1))
        require_match = bool(self.config.get("reveal_require_title_match", False)) and bool(self._titles())
        big = len(self._titles()) + 1
        ranked: List[Tuple[int, int, Contact]] = []
        for i, c in enumerate(contacts):
            if self.is_excluded(c.title):
                continue
            rank = self.title_rank(c.title)
            if rank is None and require_match:
                continue
            ranked.append((big if rank is None else rank, i, c))
        ranked.sort(key=lambda t: (t[0], t[1]))
        have = calls = 0
        for _, _, contact in ranked:
            if have >= needed:
                break
            if contact.email and contact.email_status != EmailStatus.INVALID:
                have += 1
                continue
            if calls >= limit or not self._revealable(contact):
                continue
            calls += 1
            if company is not None:  # shared with complete(): one people/match budget per company
                self._completed[company.key] = self._completed.get(company.key, 0) + 1
            contact.data["apollo_revealed"] = True
            try:
                person = self._match({"id": contact.data["apollo_id"],
                                      "reveal_personal_emails": bool(self.config.get("reveal_personal_emails",
                                                                                     False))})
            except HttpError as e:
                if e.status in NOT_FOUND_STATUSES:
                    continue
                if e.status in BLOCKING_STATUSES:
                    self._match_blocked = str(e)
                self.log.warning("apollo: email reveal failed (%s); keeping %d people found without it",
                                 e, len(contacts))
                break
            except ValueError as e:  # unreadable response: never lose the people already found
                self.log.warning("apollo: email reveal failed (%s); keeping %d people found without it",
                                 e, len(contacts))
                break
            except BudgetExceeded as e:  # the search was already paid for: keep its people
                self.log.warning("apollo: %s; keeping %d people found without more reveals", e, len(contacts))
                break
            if person:
                self.apply_person(contact, person)
            if contact.email and contact.email_status != EmailStatus.INVALID:
                have += 1

    def apply_person(self, contact: Contact, person: Dict[str, Any]) -> Contact:
        """Update ``contact`` in place from a people/match result (email + missing fields)."""
        email = clean_email(person.get("email"))
        if email and not contact.email:
            contact.email = email
            contact.email_status = apollo_email_status(person.get("email_status"))
            contact.data["email_source"] = self.name
            if person.get("email_status"):
                contact.data["apollo_email_status"] = str(person.get("email_status"))
        first, last = _clean_name(person.get("first_name")), _clean_name(person.get("last_name"))
        if last and (not contact.last_name or "*" in (contact.full_name or "")):
            contact.first_name = first or contact.first_name
            contact.last_name = last
            contact.full_name = _clean_name(person.get("name")) or f"{contact.first_name} {last}".strip()
            contact.data.pop("apollo_last_name_obfuscated", None)
        if not contact.title and (person.get("title") or person.get("headline")):
            contact.title = str(person.get("title") or person.get("headline")).strip()
        if not contact.linkedin_url and person.get("linkedin_url"):
            contact.linkedin_url = str(person["linkedin_url"]).strip()
        if not contact.phone:
            contact.phone = _phone(person)
        if not contact.seniority and person.get("seniority"):
            contact.seniority = str(person["seniority"])
        if not contact.department:
            contact.department = _department(person)
        if not contact.location:
            contact.location = _location(person)
        if contact.confidence is None:
            contact.confidence = _confidence(person)
        if person.get("id"):
            contact.data.setdefault("apollo_id", str(person["id"]))
        return contact

    def match_body(self, company: Company, contact: Contact) -> Optional[Dict[str, Any]]:
        """people/match request body for ``contact`` (None when there is nothing to match on)."""
        body: Dict[str, Any] = {"reveal_personal_emails": bool(self.config.get("reveal_personal_emails", False))}
        pid = str(contact.data.get("apollo_id") or "").strip()
        if pid:
            body["id"] = pid
            return body
        first, last = _clean_name(contact.first_name), _clean_name(contact.last_name)
        if first and last and (company.domain or company.name):
            body["first_name"], body["last_name"] = first, last
            if company.domain:
                body["domain"] = company.domain
            else:
                body["organization_name"] = company.name
            if contact.linkedin_url:
                body["linkedin_url"] = contact.linkedin_url
            return body
        if contact.linkedin_url:
            body["linkedin_url"] = contact.linkedin_url
            return body
        return None

    def complete(self, company: Company, contact: Contact) -> Contact:
        if contact.email or not self._reveal_enabled():
            return contact
        if self.ctx.dry_run:
            self.log.info("dry-run: apollo reveal skipped for %s", contact.full_name or contact.key)
            return contact
        if self._match_blocked or contact.data.get("apollo_revealed") \
                or contact.data.get("apollo_has_email") is False:
            return contact
        body = self.match_body(company, contact)
        if body is None:
            return contact
        used = self._completed.get(company.key, 0)
        if used >= self._complete_limit():
            self.log.info("apollo: complete_limit (%d) reached for %s; %s not looked up",
                          self._complete_limit(), company.name, contact.full_name or contact.key)
            return contact
        self._completed[company.key] = used + 1
        try:
            person = self._match(body)
        except HttpError as e:
            if e.status in NOT_FOUND_STATUSES:
                contact.data["apollo_revealed"] = True
                self.log.info("apollo: no match for %s at %s (HTTP %d)", contact.full_name, company.name, e.status)
                return contact
            if e.status in BLOCKING_STATUSES:
                # e.g. a plan without enrichment access / out of credits: stop people/match
                # only - the people search keeps working for the other companies
                self._match_blocked = str(e)
                self.log.warning("apollo: email lookup refused (%s); no more people/match calls this run", e)
                return contact
            raise
        contact.data["apollo_revealed"] = True
        if person:
            self.apply_person(contact, person)
        return contact


__all__ = ["ApolloFinder", "EMAIL_STATUS_MAP", "LEGACY_SEARCH_PATH", "MATCH_PATH", "SEARCH_PATH",
           "apollo_email_status", "clean_email"]
