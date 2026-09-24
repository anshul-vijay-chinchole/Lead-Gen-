"""Shared record -> ``Company`` mapping used by every source adapter.

Sources receive "records" (CSV rows, JSON objects, scraper items, API
results) whose field names differ per provider. This module turns them into
``Company`` objects (with ``Signal``s and ``Contact``s) through a *mapping*:
canonical field -> where to read it in the record.

Mapping values
--------------
* a string: a key of the record, or a dotted path into nested data
  (``company.display_name``, ``location.area.0``, ``items[0].name``);
* a list of strings: tried in order, the first non-empty value wins;
* ``None`` / ``""``: the field is disabled (useful to switch off a preset or
  an auto-detected column).

Canonical fields
----------------
Company:  name, domain, website, linkedin_url, location, city, state, country,
          industry, employees, description, keywords
Person:   first_name, last_name, full_name, title, email, email_status,
          person_linkedin_url, phone, seniority, department,
          person_location, person_city, person_state, person_country
Signal:   signal_type, signal_title, signal_date, signal_url, signal_location,
          signal_description, signal_id
Funding:  funding_stage, funding_date, funding_amount, funding_currency,
          funding_total, funding_url  (-> a ``funding`` signal titled e.g.
          "Raised Series A ($12M)")
Extras:   ``data.<key>`` -> ``Company.data[key]``, ``signal_data.<key>`` ->
          ``Signal.data[key]``, ``person_data.<key>`` -> ``Contact.data[key]``.

Common spellings are accepted as aliases: ``company_name`` -> ``name``,
``company_linkedin_url`` -> ``linkedin_url``, ``person_title`` -> ``title``,
``contact_email`` -> ``email`` ... (``company_`` / ``organization_`` prefixes
for company fields, ``person_`` / ``contact_`` for person fields). Unknown
field names raise ``ValueError`` so typos in a playbook surface immediately.

Other building blocks
---------------------
* ``defaults``: canonical field -> constant used when the record has no value
  (e.g. ``{"signal_type": "job_posting", "country": "DE"}``).
* ``signal_title_template``: ``str.format`` template rendered against the
  record (dotted paths allowed, missing keys -> ``""``), e.g.
  ``"Rated {totalScore} with {reviewsCount} reviews"``. Used when the record
  has no ``signal_title``. A template whose fields are *all* empty yields no
  signal.
* ``signal_requires``: record paths that must all be non-empty for the
  record's own signal to be created (e.g. ``["totalScore"]`` so places without
  a rating get no "review" signal).
* ``default_signal``: ``{type, title, description?, url?, date?}`` attached to
  every record that produced no signal of its own (turns a static list into
  signal-carrying companies). Its title may be a template too.
* ``people``: ``{path, mapping}`` - a list of person objects inside each record
  (e.g. a hiring team); each becomes a ``Contact``.

Records of the same company (same domain, else same normalized name) are
grouped into one ``Company``; ``limit`` caps the number of companies.
Titles/descriptions are stripped of HTML tags + entities and descriptions
are truncated to ``DESCRIPTION_LIMIT`` characters.
"""
from __future__ import annotations

import html
import re
import string
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from ..models import Company, Contact, EmailStatus, Signal, SignalType
from ..utils import (
    get_path,
    is_personal_email,
    is_valid_email,
    normalize_company_name,
    normalize_domain,
    parse_date,
    truncate,
)

DESCRIPTION_LIMIT = 1500
TITLE_LIMIT = 300
NAME_LIMIT = 200

COMPANY_FIELDS: Tuple[str, ...] = (
    "name", "domain", "website", "linkedin_url", "location", "city", "state", "country",
    "industry", "employees", "description", "keywords",
)
PERSON_FIELDS: Tuple[str, ...] = (
    "first_name", "last_name", "full_name", "title", "email", "email_status",
    "person_linkedin_url", "phone", "seniority", "department",
    "person_location", "person_city", "person_state", "person_country",
)
SIGNAL_FIELDS: Tuple[str, ...] = (
    "signal_type", "signal_title", "signal_date", "signal_url", "signal_location",
    "signal_description", "signal_id",
)
FUNDING_FIELDS: Tuple[str, ...] = (
    "funding_stage", "funding_date", "funding_amount", "funding_currency", "funding_total",
    "funding_url",
)
CANONICAL_FIELDS: Tuple[str, ...] = COMPANY_FIELDS + PERSON_FIELDS + SIGNAL_FIELDS + FUNDING_FIELDS
DATA_PREFIXES: Tuple[str, ...] = ("data.", "signal_data.", "person_data.")

# person-context spellings used inside a ``people.mapping`` (a list of people)
_PERSON_CONTEXT = {
    "linkedin_url": "person_linkedin_url", "linkedin": "person_linkedin_url",
    "location": "person_location", "city": "person_city", "state": "person_state",
    "country": "person_country", "name": "full_name", "role": "title",
}

FIELD_ALIASES: Dict[str, str] = {
    "company": "name", "organization": "name", "organisation": "name", "employer": "name",
    "employee_count": "employees", "num_employees": "employees", "headcount": "employees",
    "linkedin": "linkedin_url",
    "person_linkedin": "person_linkedin_url", "contact_linkedin": "person_linkedin_url",
    "person_name": "full_name", "contact_name": "full_name",
    "person_role": "title", "contact_role": "title",
    "funding_round": "funding_stage", "funding_type": "funding_stage",
}

# Email-status vocabulary of the common providers / exports.
_EMAIL_STATUS_MAP: Dict[str, str] = {}
for _s in ("verified", "valid", "deliverable", "ok", "safe", "safe_to_send", "good", "valid_email"):
    _EMAIL_STATUS_MAP[_s] = EmailStatus.VALID
for _s in ("catch_all", "catchall", "accept_all", "acceptall", "risky", "accept_all_unverifiable",
           "catch_all_email"):
    _EMAIL_STATUS_MAP[_s] = EmailStatus.RISKY
for _s in ("invalid", "bounced", "bounce", "undeliverable", "bad", "do_not_mail", "disposable",
           "spamtrap", "hard_bounce", "invalid_email"):
    _EMAIL_STATUS_MAP[_s] = EmailStatus.INVALID
for _s in ("guessed", "extrapolated", "unavailable", "unknown", "unverified", "pending",
           "not_verified", "none", "unverifiable"):
    _EMAIL_STATUS_MAP[_s] = EmailStatus.UNKNOWN

# Hosts that are never a company's own domain (profiles, maps, job boards).
BLOCKED_DOMAINS = frozenset({
    "linkedin.com", "facebook.com", "fb.com", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "tiktok.com", "pinterest.com", "google.com", "goo.gl", "g.page",
    "maps.app.goo.gl", "bit.ly", "indeed.com", "glassdoor.com", "yelp.com",
    "wa.me", "whatsapp.com", "t.me", "linktr.ee",
})

# Placeholder addresses some exports put in the email column.
_PLACEHOLDER_EMAIL_DOMAINS = frozenset({"domain.com", "example.com", "example.org", "example.net",
                                        "email.com", "test.com"})

_NULL_TOKENS = frozenset({"", "n/a", "null", "none", "nan", "-", "--", "—", "undefined", "#n/a",
                          "not available"})

_WS = re.compile(r"\s+")
_HOSTNAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")


# --- small value helpers -----------------------------------------------------

def is_blank(value: Any) -> bool:
    """True for None, empty/whitespace strings, null-ish tokens ('N/A', 'null', ...) and empty containers."""
    if value is None:
        return True
    if isinstance(value, float):
        return value != value  # NaN
    if isinstance(value, str):
        return value.strip().lower() in _NULL_TOKENS
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) == 0
    return False


def _dedupe(items: Iterable[str]) -> List[str]:
    seen, out = set(), []
    for it in items:
        k = it.lower()
        if it and k not in seen:
            seen.add(k)
            out.append(it)
    return out


def as_text(value: Any) -> str:
    """Best-effort plain string for any JSON-ish value (lists joined, dicts by 'name'-like keys)."""
    if is_blank(value) or isinstance(value, bool):
        return ""
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, dict):
        for k in ("name", "display_name", "displayName", "label", "title", "text", "value"):
            if not is_blank(value.get(k)):
                return as_text(value[k])
        return ""
    if isinstance(value, (list, tuple, set)):
        return ", ".join(_dedupe(as_text(v) for v in value))
    return str(value).strip()


_ESCAPED_TAG = re.compile(r"&lt;\s*/?\s*[a-zA-Z!]")
_SCRIPT_STYLE = re.compile(r"(?is)<(script|style)\b.*?</\1\s*>")
_COMMENT = re.compile(r"(?s)<!--.*?-->")
_TAG = re.compile(r"</?[a-zA-Z][^<>]*>|<![^<>]*>")
_BLOCK_TAG = re.compile(
    r"(?i)</?(?:p|div|br|hr|li|ul|ol|dl|dt|dd|h[1-6]|tr|td|th|table|thead|tbody|section|article|header|"
    r"footer|blockquote|pre|nav|aside|main|figure|figcaption|address)\b[^<>]*>")


def strip_html(value: Any) -> str:
    """Remove HTML tags and entities (also handles HTML that was itself HTML-escaped)."""
    text = as_text(value)
    if not text:
        return ""
    if _ESCAPED_TAG.search(text) and not _TAG.search(text):
        text = html.unescape(text)  # e.g. Greenhouse ``content``: "&lt;p&gt;Hello&lt;/p&gt;"
    if "<" in text:
        text = _SCRIPT_STYLE.sub(" ", text)
        text = _COMMENT.sub(" ", text)
        text = _BLOCK_TAG.sub(" ", text)   # block tags separate words ...
        text = _TAG.sub("", text)          # ... inline ones (<b>, <a>, <span>) do not
    text = html.unescape(text).replace("\xa0", " ").replace("​", "")
    return _WS.sub(" ", text).strip()


def clean_text(value: Any, limit: int = 0) -> str:
    """HTML-free, whitespace-collapsed text, optionally truncated to ``limit`` chars."""
    text = strip_html(value)
    return truncate(text, limit) if limit and text else text


def clean_description(value: Any, limit: int = DESCRIPTION_LIMIT) -> str:
    return clean_text(value, limit)


def clean_title(value: Any) -> str:
    return clean_text(value, TITLE_LIMIT)


def clean_url(value: Any) -> str:
    """A URL-ish string (unescaped, trimmed); ``""`` for non-URL junk."""
    text = html.unescape(as_text(value)).strip()
    if not text or any(ch.isspace() for ch in text) or not ("." in text or "://" in text):
        return ""
    return text


def clean_domain(value: Any) -> str:
    """Normalized company domain, or ``""`` if the value is not a usable company domain."""
    text = as_text(value)
    if not text:
        return ""
    d = normalize_domain(text)
    if not d or not _HOSTNAME.match(d):
        return ""
    if is_blocked_domain(d):
        return ""
    return d


def is_blocked_domain(domain: str) -> bool:
    d = (domain or "").lower()
    return any(d == b or d.endswith("." + b) for b in BLOCKED_DOMAINS)


def normalize_email_status(value: Any) -> str:
    """Map a provider/export email status to ``EmailStatus`` (valid | risky | invalid | unknown)."""
    s = re.sub(r"[^a-z0-9]+", "_", as_text(value).lower()).strip("_")
    if not s:
        return EmailStatus.UNKNOWN
    if s in _EMAIL_STATUS_MAP:
        return _EMAIL_STATUS_MAP[s]
    if any(w in s for w in ("invalid", "bounce", "undeliverable", "disposable", "spamtrap")):
        return EmailStatus.INVALID
    if "unverified" in s or "unknown" in s or "guess" in s:
        return EmailStatus.UNKNOWN
    if "catch" in s or "accept" in s or "risky" in s:
        return EmailStatus.RISKY
    if "verified" in s or "valid" in s or "deliverable" in s:
        return EmailStatus.VALID
    return EmailStatus.UNKNOWN


def clean_email(value: Any) -> str:
    """Lower-cased email, or ``""`` for invalid addresses and export placeholders."""
    text = as_text(value).strip().lower()
    if text.startswith("mailto:"):
        text = text[7:]
    if not is_valid_email(text):
        return ""
    local, _, dom = text.partition("@")
    if dom in _PLACEHOLDER_EMAIL_DOMAINS or local.startswith("email_not_unlocked"):
        return ""
    return text


def split_keywords(value: Any) -> List[str]:
    if is_blank(value):
        return []
    if isinstance(value, (list, tuple, set)):
        parts = [clean_text(v) for v in value]
    else:
        parts = [p.strip() for p in re.split(r"[,;|\n]", clean_text(value))]
    return _dedupe(p for p in parts if p and not is_blank(p))[:100]


def join_location(*parts: Any) -> str:
    return ", ".join(_dedupe(clean_text(p) for p in parts if not is_blank(p)))


# --- dates ------------------------------------------------------------------

_REL_PREFIX = re.compile(r"^(?:posted|active|updated|published|reposted|listed|added)\s*:?\s*(?:on\s+)?",
                         re.I)
_REL_RE = re.compile(
    r"^(\d+|an?|one)\s*\+?\s*"
    r"(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d|weeks?|wks?|w|months?|mos?|mo|years?|yrs?|y)"
    r"\.?\s*\+?\s*ago$"
)
_UNIT_DAYS = {"d": 1, "w": 7, "mo": 30, "y": 365}


def _unit_key(unit: str) -> str:
    if unit.startswith("mo"):
        return "mo"
    if unit.startswith(("s", "mi", "h")) or unit == "m":
        return "h"
    return unit[0]


def parse_when(value: Any, today: Optional[date] = None) -> Optional[date]:
    """Like ``utils.parse_date`` but relative phrases ('3 days ago', '30+ days ago',
    'Posted today', '2w ago') are resolved against ``today`` (the run date)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (datetime, date, int, float)):
        return parse_date(value)
    s = _WS.sub(" ", str(value)).strip()
    if not s:
        return None
    ref = today or date.today()
    s = _REL_PREFIX.sub("", s).strip() or s
    low = s.lower()
    if low in ("today", "just now", "just posted", "now", "new", "few seconds ago", "moments ago"):
        return ref
    if low == "yesterday":
        return ref - timedelta(days=1)
    m = _REL_RE.match(low)
    if m:
        qty = m.group(1)
        n = 1 if qty in ("a", "an", "one") else int(qty)
        key = _unit_key(m.group(2))
        if key == "h":
            # seconds/minutes/hours: only whole days count ("36 hours ago" -> 1 day)
            unit = m.group(2)
            days = n // 24 if unit.startswith("h") else 0
        else:
            days = n * _UNIT_DAYS[key]
        return ref - timedelta(days=days)
    return parse_date(s)


# --- money / funding ---------------------------------------------------------

_CURRENCY_SYMBOLS = "$€£¥₹₩₽₺₪₫₱₦฿"
_AMOUNT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(billion|million|thousand|bn|mm|b|m|k)?\b")
_SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mm": 1e6, "million": 1e6,
          "b": 1e9, "bn": 1e9, "billion": 1e9}


def parse_amount(value: Any) -> Tuple[Optional[float], str]:
    """'$12M' -> (12000000.0, '$'); '12,500,000' -> (12500000.0, ''); 'EUR 5.5m' -> (5500000.0, 'EUR')."""
    if value is None or isinstance(value, bool):
        return None, ""
    if isinstance(value, (int, float)):
        return float(value), ""
    s = as_text(value).strip()
    if not s:
        return None, ""
    currency = ""
    sym = next((ch for ch in s if ch in _CURRENCY_SYMBOLS), "")
    if sym:
        currency = sym
    else:
        code = re.search(r"\b([A-Z]{3})\b", s)
        if code:
            currency = code.group(1)
    low = s.lower().replace(",", "")
    m = _AMOUNT_RE.search(low)
    if not m:
        return None, currency
    n = float(m.group(1)) * _SCALE.get(m.group(2) or "", 1.0)
    return n, currency


def format_money(amount: Any, currency: str = "$") -> str:
    """12000000 -> '$12M', 1500000 -> '$1.5M', 750000 -> '$750K'; ISO codes go after ('12M EUR')."""
    n, found = parse_amount(amount)
    if n is None:
        return as_text(amount)
    cur = found or (currency or "")
    txt = ""
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= div:
            txt = f"{n / div:.1f}".rstrip("0").rstrip(".") + suffix
            break
    if not txt:
        txt = str(int(n)) if float(n).is_integer() else f"{n:.2f}"
    if cur and cur.isalpha() and len(cur) > 1:
        return f"{txt} {cur.upper()}"
    return f"{cur}{txt}"


_STAGE_WORDS = {"ipo": "IPO", "llc": "LLC", "ico": "ICO", "pe": "PE", "vc": "VC", "r&d": "R&D"}


def pretty_stage(value: Any) -> str:
    """'series_a' -> 'Series A', 'pre_seed' -> 'Pre-Seed', 'Series B' unchanged."""
    s = clean_text(value)
    if not s:
        return ""
    if s != s.lower() and "_" not in s:
        return s
    words = [w for w in re.split(r"[_\s]+", s.lower()) if w]
    out = []
    for w in words:
        if w in _STAGE_WORDS:
            out.append(_STAGE_WORDS[w])
        elif len(w) == 1:
            out.append(w.upper())
        else:
            out.append(w[:1].upper() + w[1:])
    pretty = " ".join(out)
    return pretty.replace("Pre Seed", "Pre-Seed").replace("Post Ipo", "Post-IPO")


def funding_signal(stage: Any = None, amount: Any = None, when: Any = None, *, label: str = "",
                   currency: str = "$", total: Any = None, url: Any = None,
                   today: Optional[date] = None) -> Optional[Signal]:
    """Build a ``funding`` signal ('Raised Series A ($12M)'); None when there is nothing to say."""
    stage_txt = pretty_stage(stage)
    n_amount, _ = parse_amount(amount)
    money = format_money(amount, currency) if n_amount else ""
    posted = parse_when(when, today)
    if not stage_txt and not money and posted is None:
        return None
    low = stage_txt.lower()
    if low in ("ipo", "public", "went public"):
        title = "Went public (IPO)"
    elif low.startswith("acquired"):
        title = stage_txt
    elif stage_txt and money:
        title = f"Raised {stage_txt} ({money})"
    elif stage_txt:
        title = f"Raised {stage_txt}"
    elif money:
        title = f"Raised {money}"
    else:
        title = "Raised a funding round"
    data: Dict[str, Any] = {}
    if stage_txt:
        data["stage"] = stage_txt
    if n_amount:
        data["amount"] = n_amount
    description = ""
    n_total, _ = parse_amount(total)
    if n_total:
        data["total"] = n_total
        description = f"Total funding to date: {format_money(total, currency)}"
    return Signal(type=SignalType.FUNDING, title=title, source=label, posted_at=posted,
                  url=clean_url(url), description=description, data=data)


# --- templates -----------------------------------------------------------------

class _TemplateFormatter(string.Formatter):
    """``str.format`` where every field is looked up via ``lookup`` and missing -> ''."""

    def __init__(self, lookup: Callable[[str], Any]):
        super().__init__()
        self._lookup = lookup
        self.hits = 0

    def get_field(self, field_name: str, args: Any, kwargs: Any) -> Tuple[Any, str]:
        value = self._lookup(field_name)
        if is_blank(value) or isinstance(value, bool):
            return "", field_name
        self.hits += 1
        if isinstance(value, (list, tuple, dict, set)):
            value = as_text(value)
        return value, field_name

    def convert_field(self, value: Any, conversion: Optional[str]) -> Any:
        if value == "":
            return ""
        return super().convert_field(value, conversion)

    def format_field(self, value: Any, format_spec: str) -> str:
        if value == "":
            return ""
        try:
            return format(value, format_spec)
        except (ValueError, TypeError):
            try:
                return format(as_text(value), format_spec)
            except (ValueError, TypeError):
                return as_text(value)


def validate_template(template: str, what: str = "template") -> None:
    """Raise ValueError for malformed templates ('Rated {totalScore')."""
    try:
        list(string.Formatter().parse(template))
    except ValueError as e:
        raise ValueError(f"invalid {what} {template!r}: {e}") from None


_EMPTY_BRACKETS = re.compile(r"\(\s*\)|\[\s*\]")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([)\].,;:!?])")


def render_template(template: str, record: Dict[str, Any],
                    extra: Optional[Dict[str, Any]] = None, *, require_value: bool = True) -> str:
    """Render ``template`` against ``record`` (dotted paths ok, missing -> '').

    With ``require_value`` (default) returns '' when none of the referenced
    fields had a value (so ``"Rated {totalScore} with {reviewsCount} reviews"``
    on a record without ratings does not produce "Rated  with  reviews").
    Empty brackets and spaces left before punctuation are tidied up."""
    if not template:
        return ""
    extra = extra or {}

    def lookup(name: str) -> Any:
        v = lookup_path(record, name)
        if is_blank(v) and name in extra:
            v = extra[name]
        return v

    fmt = _TemplateFormatter(lookup)
    try:
        out = fmt.format(template)
    except (ValueError, IndexError, KeyError, AttributeError):
        return ""
    has_fields = any(f is not None for _, f, _, _ in string.Formatter().parse(template))
    if require_value and has_fields and fmt.hits == 0:
        return ""
    if has_fields:
        out = _SPACE_BEFORE_PUNCT.sub(r"\1", _EMPTY_BRACKETS.sub("", out))
    return _WS.sub(" ", out).strip()


# --- mapping normalisation -------------------------------------------------------

def canonical_field(key: Any, *, person_context: bool = False) -> str:
    """Canonical field name for a mapping key (aliases resolved); ValueError if unknown."""
    raw = str(key).strip()
    low = raw.lower()
    for prefix in DATA_PREFIXES:
        if low.startswith(prefix) and len(raw) > len(prefix):
            return prefix + raw[len(prefix):]
    k = re.sub(r"[\s\-]+", "_", low)
    if person_context and k in _PERSON_CONTEXT:
        return _PERSON_CONTEXT[k]
    if k in CANONICAL_FIELDS:
        return k
    if k in FIELD_ALIASES:
        return FIELD_ALIASES[k]
    for prefix in ("company_", "organization_", "organisation_", "org_"):
        if k.startswith(prefix):
            rest = k[len(prefix):]
            if rest in COMPANY_FIELDS:
                return rest
            if rest in FIELD_ALIASES and FIELD_ALIASES[rest] in COMPANY_FIELDS:
                return FIELD_ALIASES[rest]
    for prefix in ("person_", "contact_"):
        if k.startswith(prefix):
            rest = k[len(prefix):]
            if rest in PERSON_FIELDS:
                return rest
            if "person_" + rest in PERSON_FIELDS:
                return "person_" + rest
    raise ValueError(
        f"unknown mapping field {raw!r}; expected one of: {', '.join(CANONICAL_FIELDS)} "
        f"(or data.<key>, signal_data.<key>, person_data.<key>)"
    )


def _as_paths(value: Any) -> List[str]:
    if value is None or value is False:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v is not None and str(v).strip()]
    return [str(value)]


def normalize_mapping(mapping: Optional[Dict[str, Any]], *,
                      person_context: bool = False) -> Dict[str, List[str]]:
    """Canonicalize keys and turn every value into a list of paths ([] = disabled)."""
    if mapping is None:
        return {}
    if not isinstance(mapping, dict):
        raise ValueError(f"mapping must be a mapping of field -> path, got {type(mapping).__name__}")
    out: Dict[str, List[str]] = {}
    for key, value in mapping.items():
        out[canonical_field(key, person_context=person_context)] = _as_paths(value)
    return out


def merge_mappings(*mappings: Optional[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Later mappings override earlier ones field by field (``None`` disables a field)."""
    out: Dict[str, List[str]] = {}
    for m in mappings:
        out.update(normalize_mapping(m))
    return out


def normalize_defaults(defaults: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not defaults:
        return {}
    if not isinstance(defaults, dict):
        raise ValueError("defaults must be a mapping of field -> value")
    return {canonical_field(k): v for k, v in defaults.items() if not is_blank(v)}


def lookup_path(record: Any, path: str) -> Any:
    """Exact key first (CSV headers may contain dots), then dotted / bracket path."""
    if not path:
        return None
    if isinstance(record, dict) and path in record:
        return record[path]
    if "[" in path:
        path = path.replace("[", ".").replace("]", "")
    if "." not in path:
        return None
    return get_path(record, path)


def first_value(record: Any, paths: Sequence[str]) -> Any:
    for p in paths:
        v = lookup_path(record, p)
        if not is_blank(v):
            return v
    return None


# --- grouping ----------------------------------------------------------------

class CompanyCollector:
    """Groups companies by domain (else normalized name) and enforces a max-company limit.

    Same name with *different* domains are kept apart (two different companies).
    Once ``limit`` companies are held, records of new companies are dropped
    (counted in ``dropped``) while records of known companies still merge in.
    """

    def __init__(self, limit: int = 0):
        try:
            self.limit = max(0, int(limit or 0))
        except (TypeError, ValueError):
            self.limit = 0
        self.companies: List[Company] = []
        self.dropped = 0
        self._by_domain: Dict[str, Company] = {}
        self._by_name: Dict[str, Company] = {}

    def __len__(self) -> int:
        return len(self.companies)

    @property
    def full(self) -> bool:
        return bool(self.limit) and len(self.companies) >= self.limit

    def find(self, company: Company) -> Optional[Company]:
        target = self._by_domain.get(company.domain) if company.domain else None
        if target is None:
            nname = normalize_company_name(company.name)
            cand = self._by_name.get(nname) if nname else None
            if cand is not None and not (cand.domain and company.domain and cand.domain != company.domain):
                target = cand
        return target

    def _index(self, target: Company, other: Company) -> None:
        if target.domain:
            self._by_domain.setdefault(target.domain, target)
        for n in (normalize_company_name(target.name), normalize_company_name(other.name)):
            if n:
                self._by_name.setdefault(n, target)

    def add(self, company: Optional[Company]) -> bool:
        """Merge or append; False when dropped because the limit is reached."""
        if company is None:
            return False
        target = self.find(company)
        if target is not None:
            target.merge(company)
            self._index(target, company)
            return True
        if self.full:
            self.dropped += 1
            return False
        self.companies.append(company)
        self._index(company, company)
        return True

    def extend(self, companies: Iterable[Optional[Company]]) -> None:
        for c in companies:
            self.add(c)


# --- record -> Company ---------------------------------------------------------------

def _normalize_default_signal(value: Any) -> Optional[Dict[str, Any]]:
    if is_blank(value):
        return None
    if isinstance(value, str):
        return {"type": SignalType.CUSTOM, "title": value}
    if not isinstance(value, dict):
        raise ValueError("default_signal must be a mapping with at least a 'title'")
    if is_blank(value.get("title")):
        raise ValueError("default_signal needs a 'title'")
    out = dict(value)
    out["type"] = _signal_type(value.get("type")) or SignalType.CUSTOM
    validate_template(str(out["title"]), "default_signal.title")
    return out


def _signal_type(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", as_text(value).lower()).strip("_")


def _normalize_people(people: Any) -> Optional[Tuple[str, Dict[str, List[str]]]]:
    if is_blank(people):
        return None
    if isinstance(people, str):
        people = {"path": people}
    if not isinstance(people, dict) or is_blank(people.get("path")):
        raise ValueError("people must be a mapping with a 'path' (list of person objects) "
                         "and an optional 'mapping'")
    default_map = {"first_name": "first_name", "last_name": "last_name", "full_name": ["full_name", "name"],
                   "title": ["title", "role", "job_title"], "email": "email",
                   "email_status": "email_status", "person_linkedin_url": ["linkedin_url", "linkedin"],
                   "phone": "phone", "seniority": "seniority", "department": "department",
                   "person_location": "location"}
    spec = dict(default_map)
    spec.update(normalize_mapping(people.get("mapping"), person_context=True))
    bad = [k for k in spec if k not in PERSON_FIELDS and not k.startswith("person_data.")]
    if bad:
        raise ValueError(f"people.mapping may only use person fields, got: {', '.join(bad)}")
    return str(people["path"]), {k: _as_paths(v) for k, v in spec.items()}


class RecordMapper:
    """Turns records into ``Company`` objects according to a mapping (see module docstring).

    Parameters: ``mapping`` (canonical field -> path(s)), ``label`` (source label
    set on companies/signals/contacts), ``defaults`` (field -> constant),
    ``today`` (resolves relative dates), ``signal_title_template``,
    ``signal_requires``, ``default_signal``, ``people`` ({path, mapping}) and
    ``location_from_signal`` (use the signal's location as company location when
    the record has none).
    """

    def __init__(self, mapping: Optional[Dict[str, Any]], *, label: str,
                 defaults: Optional[Dict[str, Any]] = None, today: Optional[date] = None,
                 signal_title_template: Optional[str] = None, default_signal: Any = None,
                 people: Any = None, location_from_signal: bool = True,
                 signal_requires: Optional[Sequence[str]] = None):
        self.mapping = normalize_mapping(mapping)
        self.label = label
        self.defaults = normalize_defaults(defaults)
        self.today = today
        self.template = str(signal_title_template or "")
        if self.template:
            validate_template(self.template, "signal_title_template")
        self.default_signal = _normalize_default_signal(default_signal)
        self.people = _normalize_people(people)
        self.location_from_signal = location_from_signal
        if isinstance(signal_requires, str):
            signal_requires = [signal_requires]
        self.signal_requires = [str(p) for p in (signal_requires or []) if str(p).strip()]

    # value access ---------------------------------------------------------------
    def _getter(self, record: Dict[str, Any], defaults: Dict[str, Any]) -> Callable[[str], Any]:
        def val(field: str) -> Any:
            v = first_value(record, self.mapping.get(field, ()))
            if is_blank(v):
                v = defaults.get(field)
            return None if is_blank(v) else v
        return val

    def _data(self, record: Dict[str, Any], prefix: str) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for field, paths in self.mapping.items():
            if field.startswith(prefix):
                v = first_value(record, paths)
                if not is_blank(v):
                    out[field[len(prefix):]] = v
        return out

    # contacts ------------------------------------------------------------------------
    def _contact(self, val: Callable[[str], Any], data: Optional[Dict[str, Any]] = None) -> Optional[Contact]:
        first, last = clean_text(val("first_name"), 100), clean_text(val("last_name"), 100)
        full = clean_text(val("full_name"), NAME_LIMIT)
        email = clean_email(val("email"))
        linkedin = clean_url(val("person_linkedin_url"))
        if not (first or last or full or email or linkedin):
            return None
        return Contact(
            first_name=first, last_name=last, full_name=full,
            title=clean_title(val("title")),
            email=email,
            email_status=normalize_email_status(val("email_status")) if email else EmailStatus.UNKNOWN,
            linkedin_url=linkedin,
            phone=as_text(val("phone")),
            seniority=clean_text(val("seniority"), 100),
            department=clean_text(val("department"), 200),
            location=clean_text(val("person_location"), 200) or join_location(
                val("person_city"), val("person_state"), val("person_country")),
            source=self.label,
            data=data or {},
        )

    def _people(self, record: Dict[str, Any]) -> List[Contact]:
        if not self.people:
            return []
        path, spec = self.people
        items = lookup_path(record, path)
        if isinstance(items, dict):
            items = [items]
        out: List[Contact] = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue

            def val(field: str, _item: Dict[str, Any] = item) -> Any:
                v = first_value(_item, spec.get(field, ()))
                return None if is_blank(v) else v

            pdata = {f[len("person_data."):]: first_value(item, p) for f, p in spec.items()
                     if f.startswith("person_data.") and not is_blank(first_value(item, p))}
            c = self._contact(val, pdata)
            if c is not None:
                out.append(c)
        return out

    # signals ----------------------------------------------------------------------------
    def _signal(self, record: Dict[str, Any], val: Callable[[str], Any],
                canon: Dict[str, Any]) -> Optional[Signal]:
        if any(is_blank(lookup_path(record, p)) for p in self.signal_requires):
            return None
        title = clean_title(val("signal_title"))
        if not title and self.template:
            title = clean_title(render_template(self.template, record, canon))
        if not title:
            return None
        return Signal(
            type=_signal_type(val("signal_type")) or SignalType.CUSTOM,
            title=title,
            source=self.label,
            posted_at=parse_when(val("signal_date"), self.today),
            url=clean_url(val("signal_url")),
            location=clean_text(val("signal_location"), 200),
            description=clean_description(val("signal_description")),
            external_id=as_text(val("signal_id")),
            data=self._data(record, "signal_data."),
        )

    def _default_signal(self, record: Dict[str, Any], canon: Dict[str, Any]) -> Optional[Signal]:
        ds = self.default_signal
        if not ds:
            return None
        title = clean_title(render_template(str(ds["title"]), record, canon, require_value=False))
        if not title:
            return None
        desc = ds.get("description")
        return Signal(
            type=ds["type"], title=title, source=self.label,
            posted_at=parse_when(ds.get("date") or ds.get("posted_at"), self.today),
            url=clean_url(ds.get("url")),
            description=clean_description(render_template(str(desc), record, canon,
                                                          require_value=False)) if desc else "",
            data=dict(ds.get("data") or {}),
        )

    # company -------------------------------------------------------------------------------
    def to_company(self, record: Dict[str, Any],
                   extra_defaults: Optional[Dict[str, Any]] = None) -> Optional[Company]:
        """Build a Company from one record; None if it has neither a name nor a domain."""
        if not isinstance(record, dict):
            return None
        defaults = dict(self.defaults)
        if extra_defaults:
            defaults.update(normalize_defaults(extra_defaults))
        val = self._getter(record, defaults)

        name = clean_text(val("name"), NAME_LIMIT)
        linkedin = clean_url(val("linkedin_url"))
        website = clean_url(val("website"))
        domain = clean_domain(val("domain"))
        if website:
            host = normalize_domain(website)
            if is_blocked_domain(host):
                if "linkedin.com" in host and not linkedin:
                    linkedin = website
                website = ""
            elif not clean_domain(website):
                website = ""
        domain = domain or clean_domain(website)

        contacts: List[Contact] = []
        main = self._contact(val, self._data(record, "person_data."))
        if main is not None:
            contacts.append(main)
        contacts.extend(self._people(record))
        if not domain:
            for ct in contacts:
                if ct.email and not is_personal_email(ct.email):
                    domain = clean_domain(ct.email)
                    if domain:
                        break
        if not name and not domain:
            return None
        if not name:
            name = domain

        city, state, country = (clean_text(val(f), 100) for f in ("city", "state", "country"))
        location = clean_text(val("location"), 300) or join_location(city, state, country)
        data = self._data(record, "data.")
        if city:
            data.setdefault("city", city)
        if state:
            data.setdefault("state", state)

        canon = {"name": name, "domain": domain, "city": city, "state": state, "country": country,
                 "location": location}
        signals: List[Signal] = []
        sig = self._signal(record, val, canon)
        if sig is not None:
            signals.append(sig)
            if not location and sig.location and self.location_from_signal:
                location = sig.location
        fund = funding_signal(val("funding_stage"), val("funding_amount"), val("funding_date"),
                              label=self.label, currency=as_text(val("funding_currency")) or "$",
                              total=val("funding_total"), url=val("funding_url"), today=self.today)
        if sig is None:
            dsig = self._default_signal(record, canon)
            if dsig is not None:
                signals.append(dsig)
        if fund is not None:
            signals.append(fund)

        employees = val("employees")
        company = Company(
            name=name,
            domain=domain,
            website=website,
            linkedin_url=linkedin,
            location=location,
            country=country,
            industry=clean_text(val("industry"), 200),
            employees=employees if not isinstance(employees, (list, dict, bool)) else None,
            description=clean_description(val("description")),
            keywords=split_keywords(val("keywords")),
            sources=[self.label],
            data=data,
        )
        for s in signals:
            if all((s.fingerprint, s.external_id) != (o.fingerprint, o.external_id) for o in company.signals):
                company.signals.append(s)
        seen = set()
        for ct in contacts:
            if ct.key not in seen:
                seen.add(ct.key)
                company.contacts.append(ct)
        return company


def records_to_companies(records: Iterable[Dict[str, Any]], mapping: Optional[Dict[str, Any]], *,
                         label: str, defaults: Optional[Dict[str, Any]] = None,
                         today: Optional[date] = None, limit: int = 0,
                         signal_title_template: Optional[str] = None, default_signal: Any = None,
                         people: Any = None, location_from_signal: bool = True,
                         signal_requires: Optional[Sequence[str]] = None,
                         log: Any = None) -> List[Company]:
    """Map records to grouped ``Company`` objects (see module docstring)."""
    mapper = RecordMapper(mapping, label=label, defaults=defaults, today=today,
                          signal_title_template=signal_title_template, default_signal=default_signal,
                          people=people, location_from_signal=location_from_signal,
                          signal_requires=signal_requires)
    collector = CompanyCollector(limit)
    total = skipped = 0
    for rec in records:
        total += 1
        company = mapper.to_company(rec) if isinstance(rec, dict) else None
        if company is None:
            skipped += 1
            continue
        collector.add(company)
    if log is not None:
        if skipped:
            log.info("%s: skipped %d of %d records without a company name or domain", label, skipped, total)
        if collector.dropped:
            log.info("%s: limit %d reached, dropped %d more companies", label, collector.limit,
                     collector.dropped)
    return collector.companies


# --- header auto-detection (CSV / flat JSON) ------------------------------------------------

def record_keys(records: Sequence[Dict[str, Any]], sample: int = 200) -> List[str]:
    """Ordered union of the top-level keys of the first ``sample`` records (for auto-detection)."""
    keys: List[str] = []
    seen: set = set()
    for rec in list(records)[:sample]:
        if not isinstance(rec, dict):
            continue
        for k in rec:
            if k not in seen and normalize_header(k):
                seen.add(k)
                keys.append(str(k))
    return keys


def normalize_header(header: Any) -> str:
    """'Company Name' / 'company_name' / 'COMPANY-NAME' -> 'companyname'; '# Employees' -> '#employees'."""
    return re.sub(r"[^a-z0-9#]", "", str(header or "").lower())


_H_COMPANY_NAME = ["company", "companyname", "organization", "organizationname", "organisation",
                   "organisationname", "accountname", "account", "businessname", "business",
                   "employer", "employername", "hiringcompany", "hiringorganization",
                   "companynameforemails"]
_H_DOMAIN = ["domain", "companydomain", "websitedomain", "companywebsitedomain", "primarydomain",
             "rootdomain", "emaildomain"]
_H_WEBSITE = ["website", "companywebsite", "websiteurl", "companywebsiteurl", "companyurl", "homepage",
              "web", "site", "companysite"]
_H_COMPANY_LINKEDIN = ["companylinkedinurl", "companylinkedin", "companylinkedinprofile",
                       "companylinkedinprofileurl", "linkedincompanyurl", "linkedincompanypage",
                       "organizationlinkedinurl", "companylinkedinpage"]
_H_AMBIG_LINKEDIN = ["linkedinurl", "linkedin", "linkedinprofile", "linkedinlink"]
_H_COMPANY_LOCATION = ["companylocation", "headquarters", "hq", "hqlocation", "companyheadquarters",
                       "companyaddress", "companyhq"]
_H_COMPANY_CITY = ["companycity", "hqcity"]
_H_COMPANY_STATE = ["companystate", "hqstate", "companyregion"]
_H_COMPANY_COUNTRY = ["companycountry", "hqcountry", "companycountrycode"]
_H_PLAIN_LOCATION = ["location", "address", "fulladdress"]
_H_PLAIN_CITY = ["city", "town"]
_H_PLAIN_STATE = ["state", "region", "province", "stateprovince"]
_H_PLAIN_COUNTRY = ["country", "countrycode", "countryname"]
_H_INDUSTRY = ["industry", "companyindustry", "industries", "sector", "vertical", "category"]
_H_EMPLOYEES = ["#employees", "employees", "numberofemployees", "noofemployees", "numemployees",
                "employeecount", "companyemployeecount", "companysize", "companyheadcount", "headcount",
                "size", "employeesize", "estimatednumemployees", "employeerange", "staffcount",
                "companyemployees", "employeesrange"]
_H_COMPANY_DESCRIPTION = ["companydescription", "shortdescription", "about", "companyabout",
                          "companysummary", "overview", "companyoverview"]
_H_KEYWORDS = ["keywords", "companykeywords", "tags", "technologies", "specialties", "specialities"]

_H_FIRST = ["firstname", "first", "givenname", "contactfirstname", "personfirstname"]
_H_LAST = ["lastname", "last", "surname", "familyname", "contactlastname", "personlastname"]
_H_FULL = ["fullname", "contactname", "personname", "contactfullname", "prospectname", "leadname",
           "personfullname"]
_H_EMAIL = ["email", "emailaddress", "workemail", "businessemail", "contactemail", "personemail",
            "email1", "primaryemail", "directemail", "professionalemail"]
_H_EMAIL_STATUS = ["emailstatus", "emailverificationstatus", "emailverification", "verificationstatus",
                   "emailvalidation", "emailvalidity", "emailresult"]
_H_PERSON_LINKEDIN = ["personlinkedinurl", "contactlinkedinurl", "linkedinprofileurl", "profileurl",
                      "personlinkedin", "contactlinkedin", "linkedinprofilelink"]
_H_PERSON_TITLE_ONLY = ["persontitle", "contacttitle", "currenttitle", "currentjobtitle",
                        "currentposition", "designation"]
_H_AMBIG_TITLE = ["title", "jobtitle", "position", "role"]
_H_PHONE = ["workdirectphone", "directphone", "directdial", "mobilephone", "mobile", "cellphone",
            "phone", "phonenumber", "workphone", "officephone", "corporatephone"]
_H_COMPANY_PHONE = ["corporatephone", "companyphone", "companyphonenumber", "mainphone"]
_H_SENIORITY = ["seniority", "senioritylevel"]
_H_DEPARTMENT = ["departments", "department", "jobfunction", "function"]

_H_SIGNAL_TYPE = ["signaltype", "signal"]
_H_SIGNAL_TITLE_ONLY = ["signaltitle", "vacancy", "vacancytitle", "positionname", "jobname",
                        "jobposition", "opening", "postingtitle", "jobpostingtitle", "openrole",
                        "openposition", "hiringfor"]
_H_SIGNAL_URL = ["signalurl", "joburl", "joblink", "jobpostingurl", "postingurl", "applyurl",
                 "applicationurl", "applylink", "vacancyurl", "jobpostinglink"]
_H_SIGNAL_DATE = ["signaldate", "dateposted", "posted", "postedat", "posteddate", "postedon",
                  "postingdate", "publishedat", "datepublished", "publishdate", "published", "listeddate",
                  "jobposteddate"]
_H_SIGNAL_DATE_JOBS = ["created", "createdat", "date", "listed"]
_H_SIGNAL_LOCATION = ["signallocation", "joblocation", "vacancylocation", "postinglocation"]
_H_SIGNAL_DESCRIPTION = ["signaldescription", "jobdescription", "vacancydescription",
                         "postingdescription"]
_H_SIGNAL_ID = ["signalid", "jobid", "postingid", "vacancyid", "jobreference", "jobref"]

_H_FUNDING_STAGE = ["latestfunding", "latestfundingstage", "latestfundinground", "latestfundingtype",
                    "fundingstage", "lastfundingtype", "lastfundingstage", "lastfundinground",
                    "fundinground", "fundingtype"]
_H_FUNDING_AMOUNT = ["latestfundingamount", "lastfundingamount", "fundingamount", "lastroundamount",
                     "latestroundamount", "roundamount", "amountraised", "lastfundingroundamount"]
_H_FUNDING_DATE = ["lastraisedat", "latestfundingdate", "lastfundingdate", "fundingdate", "lastfundingat",
                   "lastrounddate", "lastfundingrounddate", "latestfundingrounddate", "dateraised",
                   "raiseddate", "lastfundingon"]
_H_FUNDING_TOTAL = ["totalfunding", "totalfundingamount", "totalraised", "totalfundingusd"]


def detect_mapping(headers: Sequence[Any]) -> Tuple[Dict[str, List[str]], Dict[str, Any]]:
    """Guess a mapping from column headers / flat JSON keys.

    Understands Apollo people + company exports, Sales Navigator / Clay style
    lists and job lists. Matching is case/space/underscore-insensitive.

    Ambiguity rules:
      * the file has *person* columns (a name or email) -> ``Title`` /
        ``Job Title`` is the person's title and ``LinkedIn URL`` the person's
        profile; otherwise ``Job Title`` / ``Title`` / ``Role`` / ``Vacancy``
        is a *signal* title (``info['mode'] == 'jobs'``, default signal type
        ``job_posting``) and ``LinkedIn URL`` is the company page;
      * company-prefixed ``Company City/State/Country`` (or ``Headquarters``)
        are the company location - the plain ``City/State/Country/Location``
        columns then belong to the person; without company-prefixed columns
        the plain ones are the company location.

    Returns ``(mapping, info)``; ``info`` has ``mode`` ('people' | 'jobs' |
    'companies') and ``signal_type`` (``'job_posting'`` in jobs mode else None).
    """
    by_norm: Dict[str, str] = {}
    for h in headers:
        n = normalize_header(h)
        if n and n not in by_norm:
            by_norm[n] = str(h)
    used: set = set()
    mapping: Dict[str, List[str]] = {}

    def present(cands: Sequence[str]) -> List[str]:
        return [by_norm[c] for c in cands if c in by_norm]

    def pick(field: str, cands: Sequence[str], *, multi: bool = False, reuse: bool = False) -> List[str]:
        if field in mapping:
            return mapping[field]
        found = [h for h in present(cands) if reuse or h not in used]
        if not found:
            return []
        chosen = found if multi else found[:1]
        mapping[field] = chosen
        used.update(chosen)
        return chosen

    has_company_name_col = bool(present(_H_COMPANY_NAME))
    has_person = bool(present(_H_FIRST + _H_LAST + _H_FULL + _H_EMAIL))
    if "name" in by_norm and (has_company_name_col or has_person):
        has_person = True
    job_title_cols = present(_H_AMBIG_TITLE + _H_SIGNAL_TITLE_ONLY)
    if has_person:
        mode = "people"
    elif job_title_cols:
        mode = "jobs"
    else:
        mode = "companies"

    # company identity
    if not pick("name", _H_COMPANY_NAME, multi=True) and "name" in by_norm and not has_person:
        pick("name", ["name"])
    pick("domain", _H_DOMAIN)
    pick("website", _H_WEBSITE + (["url", "link"] if mode == "companies" else []))
    pick("linkedin_url", _H_COMPANY_LINKEDIN + (_H_AMBIG_LINKEDIN if mode != "people" else []))

    # people
    if has_person:
        pick("first_name", _H_FIRST)
        pick("last_name", _H_LAST)
        pick("full_name", _H_FULL + ["name"])
        pick("email", _H_EMAIL)
        pick("email_status", _H_EMAIL_STATUS)
        pick("person_linkedin_url", _H_PERSON_LINKEDIN + _H_AMBIG_LINKEDIN)
        pick("title", _H_PERSON_TITLE_ONLY + _H_AMBIG_TITLE)
        pick("phone", _H_PHONE, multi=True)
        pick("seniority", _H_SENIORITY)
        pick("department", _H_DEPARTMENT)
    company_phone = present(_H_COMPANY_PHONE)
    if company_phone:
        mapping["data.phone"] = company_phone[:1]

    # locations
    company_prefixed = bool(present(_H_COMPANY_LOCATION + _H_COMPANY_CITY + _H_COMPANY_STATE
                                    + _H_COMPANY_COUNTRY))
    pick("location", _H_COMPANY_LOCATION)
    pick("city", _H_COMPANY_CITY)
    pick("state", _H_COMPANY_STATE)
    pick("country", _H_COMPANY_COUNTRY)
    if has_person and company_prefixed:
        pick("person_location", _H_PLAIN_LOCATION)
        pick("person_city", _H_PLAIN_CITY)
        pick("person_state", _H_PLAIN_STATE)
        pick("person_country", _H_PLAIN_COUNTRY)
    else:
        pick("location", _H_PLAIN_LOCATION)
        pick("city", _H_PLAIN_CITY)
        pick("state", _H_PLAIN_STATE)
        pick("country", _H_PLAIN_COUNTRY)

    # firmographics
    pick("industry", _H_INDUSTRY)
    pick("employees", _H_EMPLOYEES)
    pick("keywords", _H_KEYWORDS)

    # signals
    pick("signal_type", _H_SIGNAL_TYPE)
    if mode == "jobs":
        pick("signal_title", _H_SIGNAL_TITLE_ONLY[:1] + _H_AMBIG_TITLE + _H_SIGNAL_TITLE_ONLY[1:])
        pick("signal_url", _H_SIGNAL_URL + ["url", "link"])
        pick("signal_date", _H_SIGNAL_DATE + _H_SIGNAL_DATE_JOBS)
        pick("signal_description", _H_SIGNAL_DESCRIPTION + ["description"])
        pick("signal_id", _H_SIGNAL_ID + ["id", "reference"])
        if not pick("signal_location", _H_SIGNAL_LOCATION + _H_PLAIN_LOCATION) and mapping.get("location"):
            mapping["signal_location"] = list(mapping["location"])  # the job's location = the company's
    else:
        pick("signal_title", _H_SIGNAL_TITLE_ONLY)
        pick("signal_url", _H_SIGNAL_URL)
        pick("signal_date", _H_SIGNAL_DATE)
        pick("signal_description", _H_SIGNAL_DESCRIPTION)
        pick("signal_id", _H_SIGNAL_ID)
        pick("signal_location", _H_SIGNAL_LOCATION)
    pick("description", _H_COMPANY_DESCRIPTION + (["description", "summary"] if mode != "jobs" else []))

    # funding
    pick("funding_stage", _H_FUNDING_STAGE)
    pick("funding_amount", _H_FUNDING_AMOUNT)
    pick("funding_date", _H_FUNDING_DATE)
    pick("funding_total", _H_FUNDING_TOTAL)

    mapped = {h for paths in mapping.values() for h in paths}
    info = {"mode": mode, "signal_type": SignalType.JOB_POSTING if mode == "jobs" else None,
            "unmapped": [h for h in by_norm.values() if h not in mapped]}
    return mapping, info


def resolve_columns(overrides: Optional[Dict[str, Any]], headers: Sequence[Any],
                    log: Any = None, label: str = "") -> Dict[str, List[str]]:
    """Resolve user mapping overrides (field -> column header) against the actual headers.

    Header names are matched exactly first, then case/space/underscore-insensitively;
    anything else is kept as a (dotted) path. Unknown columns log a warning."""
    spec = normalize_mapping(overrides)
    by_norm: Dict[str, str] = {}
    exact = {str(h) for h in headers}
    for h in headers:
        by_norm.setdefault(normalize_header(h), str(h))
    out: Dict[str, List[str]] = {}
    for field, paths in spec.items():
        resolved = []
        for p in paths:
            if p in exact:
                resolved.append(p)
            elif normalize_header(p) in by_norm:
                resolved.append(by_norm[normalize_header(p)])
            else:
                resolved.append(p)
                if log is not None and "." not in p:
                    log.warning("%s: mapping %s -> %r: no such column", label or "source", field, p)
        out[field] = resolved
    return out


__all__ = [
    "CANONICAL_FIELDS", "COMPANY_FIELDS", "PERSON_FIELDS", "SIGNAL_FIELDS", "FUNDING_FIELDS",
    "DESCRIPTION_LIMIT", "CompanyCollector", "RecordMapper", "records_to_companies",
    "detect_mapping", "resolve_columns", "normalize_mapping", "merge_mappings", "canonical_field",
    "normalize_email_status", "parse_when", "strip_html", "clean_text", "clean_title",
    "clean_description", "clean_domain", "clean_email", "clean_url", "funding_signal",
    "format_money", "parse_amount", "pretty_stage", "render_template", "is_blank", "as_text",
    "lookup_path", "first_value", "split_keywords", "join_location", "normalize_header", "record_keys",
]
