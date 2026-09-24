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
from urllib.parse import urlparse

from ..models import Company, Contact, EmailStatus, Signal, SignalType
from ..utils import (
    get_path,
    is_personal_email,
    is_valid_email,
    normalize_company_name,
    normalize_domain,
    normalize_text,
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
    # link shorteners / map links: the host is never the business
    "youtu.be", "t.co", "tinyurl.com", "ow.ly", "buff.ly", "rebrand.ly", "cutt.ly", "is.gd", "rb.gy",
    "shorturl.at", "tiny.cc", "t.ly", "lnkd.in", "fb.me", "m.me", "instagr.am", "wa.link", "g.co",
    "maps.apple.com",
})

# Shared booking / listing / directory / marketplace / profile platforms. A
# business "website" that is a page on one of these (``doctolib.fr/dentiste/
# paris/luc-martin``, ``booksy.com/en-us/123_salon``, ``calendly.com/acme``)
# is not the business's own domain - many unrelated businesses share the host.
# Matched on the registrable name (every country TLD) and only for URLs with a
# path/query, so the platform company itself (``calendly.com``) stays usable.
_PLATFORM_NAMES = frozenset({
    # booking / scheduling / health directories
    "doctolib", "booksy", "fresha", "vagaro", "calendly", "treatwell", "setmore", "acuityscheduling",
    "squareup", "schedulicity", "mindbodyonline", "styleseat", "genbook", "planity", "zocdoc",
    "jameda", "doctoralia", "docplanner", "znanylekarz", "miodottore", "practo", "healthgrades", "vitals",
    "ratemds", "simplybook", "timify", "salonized", "phorest", "gettimely", "youcanbookme",
    # restaurants / delivery / travel
    "opentable", "resy", "sevenrooms", "thefork", "lafourchette", "quandoo", "exploretock", "toasttab",
    "ubereats", "doordash", "grubhub", "deliveroo", "justeat", "just-eat", "lieferando", "thuisbezorgd",
    "menulog", "skipthedishes", "foodpanda", "wolt", "glovoapp", "chownow", "menufy", "slicelife",
    "tripadvisor", "booking", "airbnb", "expedia", "agoda", "trivago", "vrbo",
    # directories / reviews / marketplaces
    "yelp", "yell", "yellowpages", "pagesjaunes", "gelbeseiten", "paginegialle", "paginasamarillas",
    "houzz", "angi", "angieslist", "homeadvisor", "thumbtack", "bark", "nextdoor", "checkatrade",
    "trustatrader", "mybuilder", "ratedpeople", "trustpilot", "foursquare", "zomato", "bbb", "manta",
    "etsy", "amazon", "ebay",
    # profiles / link-in-bio / code & media hosts
    "beacons", "taplink", "lnk", "campsite", "xing", "vk", "threads", "snapchat", "reddit", "medium",
    "github", "gitlab", "behance", "dribbble", "vimeo", "soundcloud",
})
_PLATFORM_HOSTS = frozenset({"about.me", "bio.link", "apps.apple.com", "play.google.com"})
_SECOND_LEVEL = frozenset({"co", "com", "org", "net", "gov", "edu", "ac", "or", "ne", "go", "gob", "nom"})
_TRIVIAL_PATH = re.compile(r"^(?:[a-z]{2}(?:[-_][a-z]{2,4})?|index\.[a-z]{3,4}|home|default\.aspx?)?$", re.I)

# Free-mail / ISP mailbox domains (on top of ``utils.PERSONAL_EMAIL_DOMAINS``):
# an address there says nothing about the company, so it never becomes the
# company domain (two businesses both on ``sbcglobal.net`` must not merge).
_FREE_EMAIL_DOMAINS = frozenset({
    "ymail.com", "rocketmail.com", "yahoo.fr", "yahoo.de", "yahoo.es", "yahoo.it", "yahoo.ca",
    "yahoo.com.au", "yahoo.co.in", "yahoo.co.jp", "yahoo.com.br", "yahoo.com.mx", "hotmail.fr",
    "hotmail.de", "hotmail.es", "hotmail.it", "hotmail.ca", "live.co.uk", "live.fr", "live.de", "live.nl",
    "live.ca", "live.com.au", "outlook.fr", "outlook.de", "outlook.es", "outlook.it", "windowslive.com",
    "passport.com", "aim.com", "aol.co.uk", "aol.de", "aol.fr", "gmx.net", "gmx.at", "gmx.ch", "gmx.fr",
    "gmx.co.uk", "web.de", "t-online.de", "freenet.de", "arcor.de", "online.de", "posteo.de",
    "mailbox.org", "orange.fr", "wanadoo.fr", "free.fr", "sfr.fr", "neuf.fr", "laposte.net", "bbox.fr",
    "numericable.fr", "libero.it", "virgilio.it", "tin.it", "alice.it", "tiscali.it", "email.it",
    "fastwebnet.it", "telefonica.net", "terra.es", "ono.com", "kpnmail.nl", "planet.nl", "home.nl",
    "ziggo.nl", "hetnet.nl", "xs4all.nl", "telenet.be", "skynet.be", "proximus.be", "bluewin.ch",
    "hispeed.ch", "sunrise.ch", "chello.at", "aon.at", "seznam.cz", "centrum.cz", "wp.pl", "o2.pl",
    "onet.pl", "interia.pl", "op.pl", "mail.ru", "bk.ru", "list.ru", "inbox.ru", "rambler.ru",
    "yandex.ru", "ya.ru", "ukr.net", "abv.bg", "sapo.pt", "eircom.net", "sbcglobal.net", "bellsouth.net",
    "pacbell.net", "swbell.net", "ameritech.net", "prodigy.net", "flash.net", "cox.net", "charter.net",
    "optonline.net", "optimum.net", "earthlink.net", "mindspring.com", "juno.com", "netzero.net",
    "netzero.com", "frontier.com", "frontiernet.net", "windstream.net", "centurylink.net", "centurytel.net",
    "embarqmail.com", "q.com", "roadrunner.com", "rr.com", "twc.com", "spectrum.net", "suddenlink.net",
    "mediacombb.net", "cableone.net", "wowway.com", "hughes.net", "rcn.com", "shaw.ca", "rogers.com",
    "sympatico.ca", "videotron.ca", "cogeco.ca", "telus.net", "bigpond.com", "bigpond.net.au",
    "optusnet.com.au", "iinet.net.au", "tpg.com.au", "internode.on.net", "xtra.co.nz", "ntlworld.com",
    "blueyonder.co.uk", "talktalk.net", "tiscali.co.uk", "virgin.net", "btopenworld.com", "orange.net",
    "plus.net", "fsmail.net", "uol.com.br", "bol.com.br", "terra.com.br", "ig.com.br", "globo.com",
    "prodigy.net.mx", "rediffmail.com", "sify.com", "qq.com", "163.com", "126.com", "yeah.net",
    "sina.com", "sina.cn", "sohu.com", "naver.com", "hanmail.net", "daum.net", "nate.com",
    "hushmail.com", "tutanota.com", "tuta.io", "fastmail.com", "fastmail.fm", "pm.me", "gmx.us",
    "inbox.com", "lycos.com", "excite.com", "mail.ch",
})
# Webmail brands whose every country domain is free mail (``yahoo.co.nz``, ``hotmail.be``, ...).
_FREE_EMAIL_BRANDS = frozenset({"gmail", "googlemail", "yahoo", "ymail", "hotmail", "outlook", "live",
                                "msn", "aol", "gmx", "icloud", "protonmail", "yandex"})

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


def safe_host(value: Any) -> str:
    """``utils.normalize_domain`` that never raises: stray brackets ('[acme.com]',
    'acme.com]') are dropped and anything unparsable yields ``""``."""
    try:
        host = normalize_domain(value)
    except ValueError:  # urlparse: 'Invalid IPv6 URL' / "... does not appear to be an IPv4 or IPv6 address"
        host = ""
    if host or value is None or not any(b in str(value) for b in "[]"):
        return host
    try:
        return normalize_domain(str(value).replace("[", "").replace("]", ""))
    except ValueError:
        return ""


def _url_path_and_query(text: str) -> Tuple[str, str]:
    v = text.strip()
    if "://" not in v:
        v = "http://" + v
    try:
        parsed = urlparse(v.replace("[", "").replace("]", ""))
    except ValueError:
        return "", ""
    return parsed.path or "", parsed.query or ""


def registrable_name(domain: str) -> str:
    """'www.tripadvisor.co.uk' -> 'tripadvisor', 'booksy.com' -> 'booksy' (heuristic, no PSL)."""
    labels = [p for p in (domain or "").lower().split(".") if p]
    if len(labels) < 2:
        return labels[0] if labels else ""
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL:
        return labels[-3]
    return labels[-2]


def is_platform_url(value: Any) -> bool:
    """True for a page on a shared booking/listing/profile platform (see ``_PLATFORM_NAMES``)."""
    text = as_text(value)
    if not text or "@" in text:
        return False
    host = safe_host(text)
    if not host:
        return False
    if not (registrable_name(host) in _PLATFORM_NAMES
            or any(host == h or host.endswith("." + h) for h in _PLATFORM_HOSTS)):
        return False
    path, query = _url_path_and_query(text)
    return bool(query) or not _TRIVIAL_PATH.match(path.strip("/"))


def clean_domain(value: Any) -> str:
    """Normalized company domain, or ``""`` if the value is not a usable company domain."""
    text = as_text(value)
    if not text:
        return ""
    d = safe_host(text)
    if not d or not _HOSTNAME.match(d):
        return ""
    if is_blocked_domain(d) or is_platform_url(text):
        return ""
    return d


def is_blocked_domain(domain: str) -> bool:
    d = (domain or "").lower()
    return any(d == b or d.endswith("." + b) for b in BLOCKED_DOMAINS)


def is_free_email(email: str) -> bool:
    """Free-mail / ISP address (gmail, yahoo.fr, sbcglobal.net, web.de, ...): its domain is not a company's."""
    if is_personal_email(email):
        return True
    d = safe_host(email)
    if d in _FREE_EMAIL_DOMAINS:
        return True
    labels = d.split(".")
    return len(labels) >= 2 and labels[0] in _FREE_EMAIL_BRANDS and registrable_name(d) == labels[0]


_NAME_STOPWORDS = frozenset({"and", "the", "for", "und", "des", "les", "der", "die", "das", "von"})
_TRANSLIT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue",
                           "æ": "ae", "Æ": "Ae", "ø": "oe", "Ø": "Oe", "å": "aa", "Å": "Aa", "œ": "oe"})


def _words_match_domain(words: List[str], core: str, reg: str) -> bool:
    compact = "".join(words)
    if compact in core or (len(core) >= 4 and core in compact):
        return True
    if any(len(w) >= 3 and w not in _NAME_STOPWORDS and w in core for w in words):
        return True
    initials = "".join(w[0] for w in words if w not in _NAME_STOPWORDS)
    return len(initials) >= 2 and (reg == initials or (len(initials) >= 3 and reg.startswith(initials)))


def domain_matches_name(domain: str, name: str) -> bool:
    """Heuristic: could ``domain`` be the own domain of a company called ``name``?

    'acme-demo.com' ~ 'Acme Corp', 'bluedoordental.co.uk' ~ 'Blue Door Dental',
    'ibm.com' ~ 'International Business Machines', 'mueller.de' ~ 'Müller GmbH';
    'sbcglobal.net' !~ "Bob's HVAC". Names without latin letters cannot be judged
    and count as a match."""
    labels = [p for p in (domain or "").lower().split(".") if p]
    core = "".join(labels[:-1] or labels).replace("-", "")
    reg = registrable_name(domain).replace("-", "")
    variants = [normalize_text(name).split(), normalize_text(str(name or "").translate(_TRANSLIT)).split()]
    if not variants[0]:
        return True
    return bool(core) and any(_words_match_domain(words, core, reg) for words in variants if words)


_NEGATED_GOOD = re.compile(r"(?:^|_)(?:not|non|no|never)_?(?:valid|deliverable)(?:_|$)")
_NEGATED_CHECK = re.compile(r"(?:^|_)(?:not|non|no|never|un|cannot|unable)_?(?:verif|valid|deliver|confirm|check)")
_PENDING_WORDS = ("pending", "need", "require", "validation", "validating", "verification", "verifying",
                  "processing", "queue", "progress", "checking", "to_verify", "to_validate", "todo",
                  "unchecked", "not_checked", "waiting", "retry")
_GOOD_TOKENS = frozenset({"verified", "valid", "deliverable", "validated", "ok", "safe", "good"})


def normalize_email_status(value: Any) -> str:
    """Map a provider/export email status to ``EmailStatus`` (valid | risky | invalid | unknown).

    Only statuses that clearly say "valid" become VALID: negations ('Not valid',
    'non-deliverable' -> invalid; 'unvalidated', 'not verified' -> unknown) and
    pending states ('Validation pending', 'Needs validation' -> unknown) never
    do, so the configured verifier still checks those addresses. Unrecognised
    strings are UNKNOWN (verified later), never VALID."""
    s = re.sub(r"[^a-z0-9]+", "_", as_text(value).lower()).strip("_")
    if not s:
        return EmailStatus.UNKNOWN
    if s in _EMAIL_STATUS_MAP:
        return _EMAIL_STATUS_MAP[s]
    if _NEGATED_GOOD.search(s):
        return EmailStatus.INVALID
    if any(w in s for w in ("invalid", "bounce", "undeliverable", "disposable", "spamtrap")):
        return EmailStatus.INVALID
    if _NEGATED_CHECK.search(s):
        return EmailStatus.UNKNOWN
    if "unverified" in s or "unknown" in s or "guess" in s:
        return EmailStatus.UNKNOWN
    if any(w in s for w in _PENDING_WORDS):
        return EmailStatus.UNKNOWN
    if "catch" in s or "accept" in s or "risky" in s:
        return EmailStatus.RISKY
    tokens = s.split("_")
    if any(t in ("not", "non", "no", "never", "un") for t in tokens):
        return EmailStatus.UNKNOWN
    if any(t in _GOOD_TOKENS for t in tokens):
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


# numeric d/m/y or m/d/y dates ("9/5/2026", "09/05/2026 14:03") - the order is ambiguous
_SLASH_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})(?:[ T,]+\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?"
                         r"(?:\s*[ap]\.?m\.?)?)?$", re.I)
_DATE_ORDERS = {"mdy": "mdy", "m/d/y": "mdy", "us": "mdy", "month_first": "mdy",
                "dmy": "dmy", "d/m/y": "dmy", "eu": "dmy", "uk": "dmy", "day_first": "dmy"}


def normalize_date_order(value: Any) -> Optional[str]:
    """'mdy' | 'dmy' | None (= infer). Accepts 'us'/'m/d/y' and 'eu'/'uk'/'d/m/y'; ValueError otherwise."""
    if is_blank(value) or str(value).strip().lower() == "auto":
        return None
    key = str(value).strip().lower()
    if key not in _DATE_ORDERS:
        raise ValueError(f"date_order must be 'mdy' (US, 9/5/2026 = Sep 5) or 'dmy' (9/5/2026 = 9 May) "
                         f"or 'auto', got {value!r}")
    return _DATE_ORDERS[key]


def _slash_parts(value: Any) -> Optional[Tuple[int, int, int]]:
    if not isinstance(value, str):
        return None
    s = _WS.sub(" ", value).strip()
    m = _SLASH_DATE.match(_REL_PREFIX.sub("", s).strip() or s)  # also "Posted 9/5/2026"
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def infer_date_order(values: Iterable[Any]) -> Optional[str]:
    """Day/month order of a column of numeric dates: values like '9/20/2026' prove m/d/y,
    '20/9/2026' proves d/m/y (majority wins); None when no value decides it."""
    mdy = dmy = 0
    for v in values:
        parts = _slash_parts(v)
        if not parts:
            continue
        a, b, _ = parts
        if a > 12 >= b:
            dmy += 1
        elif b > 12 >= a:
            mdy += 1
    if mdy > dmy:
        return "mdy"
    if dmy > mdy:
        return "dmy"
    return None


def parse_when(value: Any, today: Optional[date] = None, date_order: Optional[str] = None) -> Optional[date]:
    """Like ``utils.parse_date`` but relative phrases ('3 days ago', '30+ days ago',
    'Posted today', '2w ago') are resolved against ``today`` (the run date).

    ``date_order`` ('mdy' | 'dmy') fixes how numeric dates like '9/5/2026' are
    read; without it a value is read d/m/y when that is a valid date, else m/d/y."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (datetime, date, int, float)):
        return parse_date(value)
    s = _WS.sub(" ", str(value)).strip()
    if not s:
        return None
    parts = _slash_parts(s)
    if parts:
        a, b, y = parts
        orders = [date_order] if date_order in ("mdy", "dmy") else ["dmy", "mdy"]
        for order in orders:
            day, month = (a, b) if order == "dmy" else (b, a)
            try:
                return date(y, month, day)
            except ValueError:
                continue
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
                   today: Optional[date] = None, date_order: Optional[str] = None) -> Optional[Signal]:
    """Build a ``funding`` signal ('Raised Series A ($12M)'); None when there is nothing to say."""
    stage_txt = pretty_stage(stage)
    n_amount, _ = parse_amount(amount)
    money = format_money(amount, currency) if n_amount else ""
    posted = parse_when(when, today, date_order)
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
                 signal_requires: Optional[Sequence[str]] = None, date_order: Optional[str] = None):
        self.mapping = normalize_mapping(mapping)
        self.date_order = normalize_date_order(date_order)
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

    def infer_date_order(self, records: Sequence[Dict[str, Any]]) -> Optional[str]:
        """Day/month order of the file's numeric signal/funding dates (see ``infer_date_order``)."""
        paths = list(self.mapping.get("signal_date", ())) + list(self.mapping.get("funding_date", ()))
        if not paths:
            return None
        return infer_date_order(lookup_path(r, p) for r in records if isinstance(r, dict) for p in paths)

    def has_ambiguous_dates(self, records: Sequence[Dict[str, Any]]) -> bool:
        paths = list(self.mapping.get("signal_date", ())) + list(self.mapping.get("funding_date", ()))
        for r in records:
            if not isinstance(r, dict):
                continue
            for p in paths:
                parts = _slash_parts(lookup_path(r, p))
                if parts and parts[0] <= 12 and parts[1] <= 12 and parts[0] != parts[1]:
                    return True
        return False

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
        first, last = fill_name_parts(first, last, full)
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
            posted_at=parse_when(val("signal_date"), self.today, self.date_order),
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
            posted_at=parse_when(ds.get("date") or ds.get("posted_at"), self.today, self.date_order),
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
        listing_url = ""
        if website:
            host = safe_host(website)
            if is_blocked_domain(host):
                if "linkedin.com" in host and not linkedin:
                    linkedin = website
                website = ""
            elif not clean_domain(website):
                if is_platform_url(website):
                    listing_url = website  # booking/listing page shared with other businesses
                website = ""
        domain = domain or clean_domain(website)

        contacts: List[Contact] = []
        main = self._contact(val, self._data(record, "person_data."))
        if main is not None:
            contacts.append(main)
        contacts.extend(self._people(record))
        email_domain = ""
        if not domain:
            # A contact's work-email domain stands in for the company domain only when it
            # plausibly *is* the company's (free-mail / ISP domains such as sbcglobal.net or
            # web.de are shared by unrelated businesses and would merge them into one).
            for ct in contacts:
                if not ct.email or is_free_email(ct.email):
                    continue
                d = clean_domain(ct.email)
                if not d:
                    continue
                if not name or domain_matches_name(d, name):
                    domain = d
                    break
                email_domain = email_domain or d
        if not name and not domain:
            return None
        if not name:
            name = domain

        city, state, country = (clean_text(val(f), 100) for f in ("city", "state", "country"))
        location = clean_text(val("location"), 300) or join_location(city, state, country)
        data = self._data(record, "data.")
        if listing_url:
            data.setdefault("listing_url", listing_url)
        if email_domain:
            data.setdefault("email_domain", email_domain)
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
                              total=val("funding_total"), url=val("funding_url"), today=self.today,
                              date_order=self.date_order)
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


def fill_name_parts(first: str, last: str, full: str) -> Tuple[str, str]:
    """Derive a missing first/last name from ``full`` when only one part is given.

    ('Lena', '', 'Lena Vogel') -> ('Lena', 'Vogel') - email finders (Hunter,
    Apollo match) need both parts. (``Contact`` itself only splits ``full_name``
    when *both* parts are empty.)"""
    if not full or (first and last) or not (first or last):
        return first, last
    tokens = full.split()
    if first:
        if full.lower().startswith(first.lower() + " "):
            last = full[len(first):].strip()
        else:
            idx = next((i for i, t in enumerate(tokens) if t.lower() == first.lower()), None)
            if idx is not None:
                last = " ".join(tokens[idx + 1:])
            elif len(tokens) > 1:
                last = " ".join(tokens[1:])
    else:
        if full.lower().endswith(" " + last.lower()):
            rest = full[: len(full) - len(last)].split()
            first = rest[0] if rest else ""
        elif len(tokens) > 1 and tokens[0].lower() != last.lower():
            first = tokens[0]
    return first, last


def records_to_companies(records: Iterable[Dict[str, Any]], mapping: Optional[Dict[str, Any]], *,
                         label: str, defaults: Optional[Dict[str, Any]] = None,
                         today: Optional[date] = None, limit: int = 0,
                         signal_title_template: Optional[str] = None, default_signal: Any = None,
                         people: Any = None, location_from_signal: bool = True,
                         signal_requires: Optional[Sequence[str]] = None,
                         log: Any = None, date_order: Optional[str] = None) -> List[Company]:
    """Map records to grouped ``Company`` objects (see module docstring).

    ``date_order`` ('mdy' | 'dmy'); unset, it is inferred from the records'
    numeric dates so one column is never read half d/m/y and half m/d/y."""
    mapper = RecordMapper(mapping, label=label, defaults=defaults, today=today,
                          signal_title_template=signal_title_template, default_signal=default_signal,
                          people=people, location_from_signal=location_from_signal,
                          signal_requires=signal_requires, date_order=date_order)
    if mapper.date_order is None:
        records = records if isinstance(records, list) else list(records)
        mapper.date_order = mapper.infer_date_order(records)
        if mapper.date_order is None and log is not None and mapper.has_ambiguous_dates(records):
            log.warning("%s: numeric dates like 5/9/2026 are ambiguous (day/month order unknown); "
                        "reading them as day/month - set 'date_order: mdy' for US-style dates", label)
        elif mapper.date_order and log is not None:
            log.debug("%s: numeric dates read as %s", label, mapper.date_order)
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
_H_INDUSTRY = ["industry", "companyindustry", "industries", "sector", "vertical", "category", "categoryname",
               "maincategory"]
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

# Columns only place / local-business exports have (Google Maps scrapers, Outscraper, ...);
# the weak ones (a CRM may have a 'Rating') count only in pairs.
_H_PLACE = ["reviewscount", "reviewcount", "numberofreviews", "totalscore", "placeid", "googleplaceid",
            "categoryname", "maincategory", "workinghours", "openinghours", "businessstatus", "googlemapsurl",
            "mapsurl", "pluscode", "locatedin"]
_H_PLACE_WEAK = ["rating", "reviews", "averagerating", "latitude", "longitude", "lat", "lng", "cid", "googleid"]
# Columns that make a bare 'title' a job title rather than a business name.
_H_JOB_EVIDENCE = (_H_SIGNAL_TITLE_ONLY + ["jobtitle", "position", "role"] + _H_SIGNAL_URL + _H_SIGNAL_DATE
                   + _H_SIGNAL_DESCRIPTION + _H_SIGNAL_ID + ["salary", "jobtype", "employmenttype",
                                                               "contracttype"])

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
    has_person_name_cols = bool(present(_H_FIRST + _H_LAST + _H_FULL))
    has_person = bool(present(_H_FIRST + _H_LAST + _H_FULL + _H_EMAIL))
    # Place / local-business exports (Google Maps scrapers, Outscraper, ...) have no
    # company column: their 'title' / 'name' column *is* the business name - not a job
    # title (-> "Saw you're hiring a Blue Door Dental") nor a person's name.
    business_col = ""
    place_like = bool(present(_H_PLACE)) or len(present(_H_PLACE_WEAK)) >= 2
    if not has_company_name_col and not has_person_name_cols and place_like:
        person_specific = present(_H_PERSON_TITLE_ONLY + _H_PERSON_LINKEDIN + _H_SENIORITY + _H_DEPARTMENT)
        if "name" in by_norm and not person_specific and not present(_H_AMBIG_TITLE):
            business_col = "name"
        elif "title" in by_norm and "name" not in by_norm and not present(_H_JOB_EVIDENCE):
            business_col = "title"
    if "name" in by_norm and (has_company_name_col or has_person):
        has_person = True
    job_title_cols = [h for h in present(_H_AMBIG_TITLE + _H_SIGNAL_TITLE_ONLY)
                      if not business_col or h != by_norm[business_col]]
    if has_person:
        mode = "people"
    elif job_title_cols:
        mode = "jobs"
    else:
        mode = "companies"

    # company identity
    if not pick("name", _H_COMPANY_NAME, multi=True):
        if business_col:
            pick("name", [business_col])
        elif "name" in by_norm and not has_person:
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
    job_signal = mode == "jobs"
    if mode == "jobs":
        pick("signal_title", _H_SIGNAL_TITLE_ONLY[:1] + _H_AMBIG_TITLE + _H_SIGNAL_TITLE_ONLY[1:])
        pick("signal_url", _H_SIGNAL_URL + ["url", "link"])
        pick("signal_date", _H_SIGNAL_DATE + _H_SIGNAL_DATE_JOBS)
        pick("signal_description", _H_SIGNAL_DESCRIPTION + ["description"])
        pick("signal_id", _H_SIGNAL_ID + ["id", "reference"])
        if not pick("signal_location", _H_SIGNAL_LOCATION + _H_PLAIN_LOCATION) and mapping.get("location"):
            mapping["signal_location"] = list(mapping["location"])  # the job's location = the company's
    else:
        if not pick("signal_title", _H_SIGNAL_TITLE_ONLY) and mode == "people":
            # a person list with BOTH 'Title' (the person's) and 'Job Title' / 'Role' / 'Position'
            # (the opening): the leftover ambiguous column is the hiring signal
            leftover = [h for h in present(_H_AMBIG_TITLE) if h not in used]
            if leftover:
                mapping["signal_title"] = leftover[:1]
                used.update(leftover[:1])
                job_signal = True
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
    info = {"mode": mode, "signal_type": SignalType.JOB_POSTING if job_signal else None,
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
    "normalize_email_status", "parse_when", "infer_date_order", "normalize_date_order", "strip_html",
    "clean_text", "clean_title", "clean_description", "clean_domain", "clean_email", "clean_url",
    "safe_host", "is_platform_url", "is_free_email", "domain_matches_name", "fill_name_parts", "funding_signal",
    "format_money", "parse_amount", "pretty_stage", "render_template", "is_blank", "as_text",
    "lookup_path", "first_value", "split_keywords", "join_location", "normalize_header", "record_keys",
]
