"""Small, dependency-free helpers used across the package."""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any, Iterable, List, Optional
from urllib.parse import urlparse

_WS = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")

# Legal suffixes stripped when comparing company names.
_COMPANY_SUFFIXES = {
    "inc", "incorporated", "llc", "l l c", "ltd", "limited", "plc", "corp",
    "corporation", "co", "company", "gmbh", "ag", "sa", "sas", "bv", "nv",
    "pty", "pte", "llp", "lp", "group", "holdings", "the",
}

PERSONAL_EMAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "hotmail.com",
    "hotmail.co.uk", "outlook.com", "live.com", "msn.com", "aol.com",
    "icloud.com", "me.com", "mac.com", "proton.me", "protonmail.com",
    "gmx.com", "gmx.de", "mail.com", "yandex.com", "zoho.com", "btinternet.com",
    "sky.com", "virginmedia.com", "comcast.net", "verizon.net", "att.net",
})

EMAIL_RE = re.compile(r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
EMAIL_FIND_RE = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def normalize_text(text: Any) -> str:
    """Lowercase, strip accents/punctuation, collapse whitespace."""
    if text is None:
        return ""
    t = strip_accents(str(text)).lower()
    t = _NON_ALNUM.sub(" ", t)
    return _WS.sub(" ", t).strip()


def normalize_company_name(name: Any) -> str:
    words = normalize_text(name).split()
    while words and words[-1] in _COMPANY_SUFFIXES:
        words.pop()
    while words and words[0] == "the":
        words.pop(0)
    return " ".join(words)


def normalize_domain(value: Any) -> str:
    """'https://www.Acme.com/about' -> 'acme.com'. Emails -> their domain."""
    if not value:
        return ""
    v = str(value).strip().lower()
    if "@" in v and "/" not in v:
        v = v.split("@", 1)[1]
    if "://" not in v:
        v = "http://" + v
    host = urlparse(v).hostname or ""
    if host.startswith("www."):
        host = host[4:]
    return host.strip(".")


def company_key(name: Any, domain: Any = "") -> str:
    d = normalize_domain(domain)
    return d if d else "name:" + normalize_company_name(name)


def parse_date(value: Any) -> Optional[date]:
    """Parse ISO strings, datetimes, unix timestamps (s or ms), and 'N days ago'."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:  # milliseconds
            ts /= 1000.0
        try:
            return datetime.utcfromtimestamp(ts).date()
        except (OverflowError, OSError, ValueError):
            return None
    s = str(value).strip()
    if not s:
        return None
    if s.isdigit() and len(s) >= 9:
        return parse_date(int(s))
    m = re.match(r"^(\d+)\+?\s*(day|week|month|hour|minute)s?\s+ago$", s.lower())
    if m:
        n, unit = int(m.group(1)), m.group(2)
        days = {"day": n, "week": 7 * n, "month": 30 * n}.get(unit, 0)
        return date.today() - timedelta(days=days)
    if s.lower() in ("today", "just now", "just posted"):
        return date.today()
    if s.lower() == "yesterday":
        return date.today() - timedelta(days=1)
    iso = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(iso).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d %b %Y", "%b %d, %Y",
                "%d %B %Y", "%B %d, %Y", "%Y-%m-%dT%H:%M:%S.%f%z", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", s)
    if m:
        return parse_date(m.group(1))
    return None


def to_int(value: Any) -> Optional[int]:
    """'1,200' -> 1200, '51-200' -> 51, '10k' -> 10000, None -> None."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().lower().replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*([km]?)", s)
    if not m:
        return None
    n = float(m.group(1))
    n *= {"k": 1_000, "m": 1_000_000}.get(m.group(2), 1)
    return int(n)


def is_valid_email(email: Any) -> bool:
    return bool(email) and bool(EMAIL_RE.match(str(email).strip().lower()))


def is_personal_email(email: str) -> bool:
    return normalize_domain(email) in PERSONAL_EMAIL_DOMAINS


def contains_any(text: Any, needles: Iterable[str]) -> Optional[str]:
    """Return the first needle found in text (word-boundary, case/accents-insensitive)."""
    hay = f" {normalize_text(text)} "
    for n in needles:
        nn = normalize_text(n)
        if nn and f" {nn} " in hay:
            return n
    return None


def get_path(obj: Any, path: str, default: Any = None) -> Any:
    """Read a dotted path from nested dicts/lists: 'company.location.0.name'."""
    cur = obj
    for part in path.split("."):
        if cur is None:
            return default
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, (list, tuple)) and part.lstrip("-").isdigit():
            idx = int(part)
            cur = cur[idx] if -len(cur) <= idx < len(cur) else None
        else:
            return default
    return default if cur is None else cur


def first_nonempty(*values: Any) -> Any:
    for v in values:
        if v not in (None, "", [], {}):
            return v
    return None


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'’-]+\b", text or ""))


def truncate(text: str, limit: int) -> str:
    text = _WS.sub(" ", text or "").strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def chunks(items: List[Any], size: int) -> Iterable[List[Any]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]
