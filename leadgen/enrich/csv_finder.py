"""Contacts from your own CSV file (``type: csv``). Offline: never touches the network.

Use it for lists you already have (a CRM export, a purchased list, a Sales
Navigator / Clay / Apollo people export): ``find(company)`` returns the people
in the file who work at ``company``.

Matching: rows are indexed by domain - the ``domain`` column, else the
``website`` column, else the domain of the row's email when that is not a
free-mail address (gmail.com, ...) - and by normalised company name
(``utils.normalize_company_name``: case, accents, punctuation and legal
suffixes such as Inc / Ltd / GmbH ignored). A company with a domain gets the
rows for that domain plus same-name rows that have no domain of their own
(same name + different domain = a different company); a company without a
domain gets every row with the same normalised name.

Recognised columns (headers are matched case/space/underscore/punctuation-
insensitively; the first alias present wins):

=============  ==============================================================
field          header aliases
=============  ==============================================================
company        company, company name, organization, organisation, account ...
domain         domain, company domain, email domain
website        website, company website, company url, web, homepage
first_name     first name, firstname, first, given name
last_name      last name, lastname, last, surname, family name
full_name      full name, name, contact name, person, contact
title          title, job title, position, role, designation
email          email, email address, work email, business email, e-mail ...
email_status   email status, status, verification, verification status ...
linkedin_url   linkedin, linkedin url, person linkedin url, linkedin profile
phone          phone, phone number, mobile, direct phone, work phone ...
seniority      seniority, seniority level
department     department, departments, function
location       location, city, person location
=============  ==============================================================

``email_status`` values are mapped: valid / verified / deliverable / ok ->
valid; risky / catch-all / accept-all -> risky; invalid / bounced /
undeliverable -> invalid; anything else -> unknown. Unmapped non-empty columns
are kept in ``contact.data['csv']``. Rows with neither a name nor an email are
skipped.

The file is read and indexed once (on the first ``find``) and each call
returns fresh ``Contact`` objects, so callers may modify them freely.

Config keys
-----------
path (required)  CSV file (relative paths: working dir, then the playbook's dir).
mapping          Field -> header overrides, e.g. ``{title: "Job Function"}``;
                 a list means "first header present"; null disables a field.
delimiter        Default: sniffed among ``,`` ``;`` tab ``|`` (also accepts
                 ``tab`` / ``comma`` / ``semicolon`` / ``pipe``).
encoding         Default ``utf-8-sig``; ``cp1252`` then ``latin-1`` are tried
                 when it fails to decode.
limit            Max contacts returned per company (default 0 = all).
"""
from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..models import Company, Contact, EmailStatus
from ..utils import is_personal_email, is_valid_email, normalize_company_name, normalize_domain, normalize_text
from .base import ContactFinder

FIELD_ALIASES: Dict[str, Tuple[str, ...]] = {
    "company": ("company", "company name", "companyname", "organization", "organization name",
                "organisation", "organisation name", "account", "account name", "employer", "company_name"),
    "domain": ("domain", "company domain", "email domain", "organization domain", "primary domain"),
    "website": ("website", "company website", "company url", "web", "website url", "homepage"),
    "first_name": ("first name", "firstname", "first", "given name", "forename"),
    "last_name": ("last name", "lastname", "last", "surname", "family name"),
    "full_name": ("full name", "fullname", "name", "contact name", "person", "contact", "person name"),
    "title": ("title", "job title", "jobtitle", "position", "role", "designation", "headline"),
    "email": ("email", "email address", "work email", "business email", "e mail", "mail",
              "professional email", "contact email"),
    "email_status": ("email status", "emailstatus", "status", "verification", "verification status",
                     "email verification", "email verification status"),
    "linkedin_url": ("linkedin", "linkedin url", "person linkedin url", "linkedin profile",
                     "linkedin profile url", "profile url", "li url"),
    "phone": ("phone", "phone number", "mobile", "mobile phone", "direct phone", "work phone", "telephone"),
    "seniority": ("seniority", "seniority level"),
    "department": ("department", "departments", "function", "job function"),
    "location": ("location", "city", "person location", "contact location"),
}

STATUS_VALUES: Dict[str, str] = {
    "valid": EmailStatus.VALID, "verified": EmailStatus.VALID, "deliverable": EmailStatus.VALID,
    "ok": EmailStatus.VALID, "safe": EmailStatus.VALID,
    "risky": EmailStatus.RISKY, "catch all": EmailStatus.RISKY, "catchall": EmailStatus.RISKY,
    "accept all": EmailStatus.RISKY, "acceptall": EmailStatus.RISKY,
    "invalid": EmailStatus.INVALID, "bounced": EmailStatus.INVALID, "bounce": EmailStatus.INVALID,
    "undeliverable": EmailStatus.INVALID, "bad": EmailStatus.INVALID,
}

# Hosts that identify a profile page, never the employer.
_NOT_COMPANY_DOMAINS = frozenset({"linkedin.com", "facebook.com", "twitter.com", "x.com", "instagram.com",
                                  "github.com", "youtube.com", "crunchbase.com"})
_DELIMITER_NAMES = {"tab": "\t", "\\t": "\t", "comma": ",", "semicolon": ";", "pipe": "|"}
_DELIMITER_CANDIDATES = ",;\t|"
_FALLBACK_ENCODINGS = ("cp1252", "latin-1")


def _header_key(header: Any) -> str:
    return normalize_text(str(header or "").replace("_", " "))


def _company_domain(value: Any) -> str:
    """Domain of a domain/website cell, '' for profile hosts (linkedin.com/company/..., ...)."""
    domain = normalize_domain(value)
    if not domain or any(domain == d or domain.endswith("." + d) for d in _NOT_COMPANY_DOMAINS):
        return ""
    return domain


def map_status(value: Any) -> str:
    return STATUS_VALUES.get(normalize_text(value), EmailStatus.UNKNOWN)


class CsvFinder(ContactFinder):
    """People from a local CSV, matched to companies by domain / name (see module docstring)."""

    name = "csv"
    env_key = ""
    offline = True

    def __init__(self, config: Dict[str, Any], ctx: Any):
        super().__init__(config, ctx)
        self._by_domain: Optional[Dict[str, List[Dict[str, Any]]]] = None
        self._by_name: Dict[str, List[Dict[str, Any]]] = {}

    # --- loading -------------------------------------------------------------------
    def _path(self) -> Path:
        raw = self.config.get("path")
        if not raw:
            raise ValueError("csv finder: 'path' is required (the contacts CSV file)")
        path = self.ctx.playbook.resolve_path(str(raw))
        if not path.is_file():
            raise FileNotFoundError(f"csv finder: contacts file not found: {path}")
        return path

    def _read_text(self, path: Path) -> str:
        encoding = str(self.config.get("encoding") or "utf-8-sig")
        data = path.read_bytes()
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            if self.config.get("encoding"):
                raise
        for enc in _FALLBACK_ENCODINGS:
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
        return data.decode("latin-1", errors="replace")  # pragma: no cover - latin-1 never fails

    def _delimiter(self, text: str) -> str:
        raw = self.config.get("delimiter")
        if raw:
            d = _DELIMITER_NAMES.get(str(raw).lower(), str(raw))
            if len(d) != 1:
                raise ValueError(f"csv finder: delimiter must be one character, got {raw!r}")
            return d
        sample = "\n".join(text.splitlines()[:20])
        try:
            return csv.Sniffer().sniff(sample, delimiters=_DELIMITER_CANDIDATES).delimiter
        except csv.Error:
            return ","

    def _columns(self, headers: List[str]) -> Dict[str, str]:
        """Field -> actual header."""
        by_key: Dict[str, str] = {}
        for h in headers:
            by_key.setdefault(_header_key(h), h)
        cols: Dict[str, str] = {}
        overrides = self.config.get("mapping") or {}
        if not isinstance(overrides, dict):
            raise ValueError("csv finder: 'mapping' must be a mapping of field -> column header")
        unknown = [f for f in overrides if f not in FIELD_ALIASES]
        if unknown:
            raise ValueError(f"csv finder: unknown mapping field(s) {unknown}; "
                             f"expected some of {sorted(FIELD_ALIASES)}")
        for field, aliases in FIELD_ALIASES.items():
            if field in overrides:
                wanted = overrides[field]
                if wanted in (None, "", False):
                    continue  # explicitly disabled
                options = wanted if isinstance(wanted, (list, tuple)) else [wanted]
                found = next((by_key[_header_key(o)] for o in options if _header_key(o) in by_key), None)
                if found is None:
                    self.log.warning("csv finder: mapped column %r for %s not in the file", wanted, field)
                    continue
                cols[field] = found
                continue
            found = next((by_key[a] for a in (_header_key(a) for a in aliases) if a in by_key), None)
            if found is not None:
                cols[field] = found
        # never let one column serve two fields through auto-detection (e.g. "name")
        seen: Dict[str, str] = {}
        for field in list(cols):
            header = cols[field]
            if header in seen and field not in overrides:
                del cols[field]
            else:
                seen.setdefault(header, field)
        return cols

    def _load(self) -> None:
        path = self._path()
        text = self._read_text(path)
        reader = csv.DictReader(io.StringIO(text), delimiter=self._delimiter(text))
        headers = [h for h in (reader.fieldnames or []) if h is not None]
        cols = self._columns(headers)
        if not any(f in cols for f in ("domain", "website", "company", "email")):
            raise ValueError(f"csv finder: {path} needs a company, domain, website or email column "
                             f"(found: {', '.join(headers) or 'no header'})")
        used = set(cols.values())
        by_domain: Dict[str, List[Dict[str, Any]]] = {}
        by_name: Dict[str, List[Dict[str, Any]]] = {}
        rows = 0
        for raw in reader:
            record = {f: str(raw.get(h) or "").strip() for f, h in cols.items()}
            if not (record.get("email") or record.get("full_name") or record.get("first_name")
                    or record.get("last_name")):
                continue
            extra = {_header_key(k): str(v).strip() for k, v in raw.items()
                     if k is not None and k not in used and v not in (None, "") and str(v).strip()}
            record["_extra"] = extra
            domain = _company_domain(record.get("domain")) or _company_domain(record.get("website"))
            email = record.get("email", "").lower()
            if not domain and is_valid_email(email) and not is_personal_email(email):
                domain = normalize_domain(email)
            record["_domain"] = domain
            name_key = normalize_company_name(record.get("company"))
            if domain:
                by_domain.setdefault(domain, []).append(record)
            if name_key:
                by_name.setdefault(name_key, []).append(record)
            rows += 1
        self._by_domain, self._by_name = by_domain, by_name
        self.log.info("csv finder: %d contacts from %s (%d domains)", rows, path, len(by_domain))

    # --- matching --------------------------------------------------------------------
    def _to_contact(self, rec: Dict[str, Any]) -> Contact:
        email = rec.get("email", "").lower()
        if email and not is_valid_email(email):
            self.log.debug("csv finder: ignoring malformed email %r", email)
            email = ""
        contact = Contact(
            first_name=rec.get("first_name", ""),
            last_name=rec.get("last_name", ""),
            full_name=rec.get("full_name", ""),
            title=rec.get("title", ""),
            email=email,
            email_status=map_status(rec.get("email_status")) if email else EmailStatus.UNKNOWN,
            linkedin_url=rec.get("linkedin_url", ""),
            phone=rec.get("phone", ""),
            seniority=rec.get("seniority", ""),
            department=rec.get("department", ""),
            location=rec.get("location", ""),
            source=self.name,
        )
        if rec.get("_extra"):
            contact.data["csv"] = dict(rec["_extra"])
        return contact

    def find(self, company: Company) -> List[Contact]:
        if self._by_domain is None:
            self._load()
        by_domain = self._by_domain or {}
        name_key = normalize_company_name(company.name)
        picked: List[Dict[str, Any]] = []
        if company.domain:
            picked.extend(by_domain.get(company.domain, []))
            if name_key:  # same-name rows without a domain of their own
                picked.extend(r for r in self._by_name.get(name_key, []) if not r["_domain"])
        elif name_key:
            picked.extend(self._by_name.get(name_key, []))
        out: List[Contact] = []
        seen = set()
        for rec in picked:
            if id(rec) in seen:
                continue
            seen.add(id(rec))
            out.append(self._to_contact(rec))
        try:
            limit = int(self.config.get("limit") or 0)
        except (TypeError, ValueError):
            raise ValueError(f"csv finder: limit must be an integer, got {self.config.get('limit')!r}") from None
        return out[:limit] if limit > 0 else out


__all__ = ["CsvFinder", "FIELD_ALIASES", "STATUS_VALUES", "map_status"]
