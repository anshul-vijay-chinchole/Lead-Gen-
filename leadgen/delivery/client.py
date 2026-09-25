"""Client files: one YAML per client of the weekly "Hiring Signal Report".

A client file (``clients/<name>.yaml``) says what one recruitment / staffing
agency wants in its weekly lead file: the roles it fills, where, which
companies to leave out, how many leads, how fresh. ``load_client`` reads and
validates it into a ``Client``; ``client_playbook`` turns it into the
``Playbook`` the pipeline runs (the base playbook supplies sources, contact
finders, the verifier and scoring; the client file supplies the targeting).

Every key is optional. ``clients/_template.yaml`` lists them all with plain
English comments (``new_client_file`` copies it). Keys read from the client
file, with their defaults:

``display_name`` ("" = the file name)
    Shown in the report and file headers.
``playbook`` (``playbooks/recruitment-delivery.yaml``)
    The base playbook. A relative path is looked up in the current folder,
    then next to the client file, then in the folder above the client
    file's folder (the project root when clients live in ``clients/``).
``contact`` ({})
    The client's contact person (``name``, ``email``, ...) - your records only.
``roles`` ([])
    Job titles the client fills -> ``signals.match_keywords`` (replaces the
    base playbook's list; empty = keep the base playbook's list).
``exclude_roles`` ([])
    Added to ``signals.exclude_keywords`` (e.g. intern, trainee).
``buyer_titles`` ([])
    Decision-maker titles, best first -> ``buyers.titles`` (replaces; only
    when given).
``locations`` / ``exclude_locations`` / ``industries`` ([])
    -> ``icp.locations`` / ``icp.exclude_locations`` / ``icp.industries``
    (replace the base playbook's; [] = anywhere / any industry).
``company_size`` ({min: null, max: null})
    Head-count range -> ``icp.employees`` (null = no limit on that side).
``exclusions`` ({companies: [], domains: [], keywords: []})
    The client's own do-not-list. ``companies`` are plain company names (not
    patterns): they act as this client's suppression list during delivery
    (see ``ledger.LedgerHooks``). ``domains`` are added to
    ``icp.exclude_domains``, ``keywords`` to ``icp.exclude_keywords``.
``leads_per_week`` (25)
    Target volume; the file holds at most this many rows. Also sets
    ``enrichment.max_companies`` to ``max(leads_per_week * 2, 10)`` unless the
    base playbook sets a lower number (``overrides`` always win).
``freshness_days`` (7)
    Only jobs posted in the last N days -> ``signals.max_age_days``.
``allow_undated`` (false) / ``drop_reposts`` (true)
    -> ``signals.allow_undated`` / ``signals.drop_reposts``.
``redelivery_days`` (null)
    null = never deliver the same company / job / contact twice; N = it may
    be delivered again once N days have passed.
``dedupe`` ([company, job, contact])
    What the delivery ledger de-duplicates on.
``tiers`` ([hot, normal])
    Which urgency tiers may be delivered (hot, normal, skip).
``emails.include_unverified`` (true)
    false = only "verified" emails are delivered (others show "not found").
``opening_line.enabled`` (false) / ``.ai`` (false) / ``.max_cost_usd`` (0.50)
    The optional "Suggested opening line" column (template line, or AI using
    the base playbook's ``writer.provider`` / ``writer.model``, capped per run).
``budget.max_paid_lookups`` (0)
    Per-run cap on paid API requests -> ``usage.max_paid_lookups``. 0 = no
    extra cap (the base playbook's own cap, if any, still applies). The
    ``--budget`` flag overrides both.
``delivery.formats`` ([csv, xlsx, html]) / ``.folder`` (``deliveries/{client}/{date}``) /
``.google_sheet`` ({spreadsheet_id: "", worksheet: "{date}", service_account_file: ""})
    Output files, where they go ({client} and {date} placeholders) and the
    optional Google Sheets push.
``branding`` ({})
    Overrides of the playbook's ``delivery.*`` branding (brand_name,
    brand_color, sender_name, sender_email, website, logo_url, footer).
``overrides`` ({})
    Advanced: any playbook section, deep-merged last (``name`` and ``mode``
    cannot be changed).

``client_playbook`` also always sets ``mode: delivery``, ``name:
client-<name>``, ``buyers.max_contacts_per_company: 1`` (unless overridden)
and removes hand-over exporters (those whose class ``scope`` is
``"outbound"``) from ``outbound.exporters``.

The small settings groups (``emails``, ``opening_line``, ``budget``,
``delivery``, ``exclusions``, ``company_size``) are dataclasses that also
answer mapping-style access: ``client.opening_line.ai`` and
``client.opening_line["ai"]`` / ``.get("ai")`` are the same thing.
"""
from __future__ import annotations

import collections.abc
import copy
import difflib
import math
import os
import re
import string
from dataclasses import dataclass, field, fields
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

import yaml

from .. import registry
from ..playbook import DEFAULTS, Playbook, PlaybookError, from_dict, load_playbook
from ..utils import normalize_domain, to_int


class ClientError(ValueError):
    """A client file is missing or invalid (the message says what to fix)."""


DEFAULT_PLAYBOOK = "playbooks/recruitment-delivery.yaml"
DEFAULT_TEMPLATE = "clients/_template.yaml"
DEFAULT_CLIENTS_DIR = "clients"
CLIENT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

DEDUPE_KINDS = ("company", "job", "contact")
TIER_NAMES = ("hot", "normal", "skip")
DELIVERY_FORMATS = ("csv", "xlsx", "html")
FOLDER_PLACEHOLDERS = ("client", "date")
GOOGLE_SHEET_KEYS = ("spreadsheet_id", "worksheet", "service_account_file")
BRANDING_KEYS = tuple(DEFAULTS["delivery"].keys())
# playbook sections an ``overrides`` block may touch (name and mode are fixed)
OVERRIDE_SECTIONS = tuple(k for k in DEFAULTS if k not in ("name", "mode"))

CLIENT_KEYS = (
    "display_name", "playbook", "contact",
    "roles", "exclude_roles", "buyer_titles",
    "locations", "exclude_locations", "company_size", "industries", "exclusions",
    "leads_per_week", "freshness_days", "allow_undated", "drop_reposts",
    "redelivery_days", "dedupe", "tiers",
    "emails", "opening_line", "budget", "delivery", "branding", "overrides",
)

_FORMAT_HINTS = {"excel": "xlsx", "xls": "xlsx", "spreadsheet": "xlsx", "htm": "html", "web": "html"}
_DOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$")
_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


# --- settings groups ---------------------------------------------------------------

class _Section:
    """Read-only mapping view of a settings group: ``s["key"]``, ``s.get("key")``,
    ``"key" in s``, ``dict(s)``, ``s.items()``, ``s.to_dict()``; equal to a dict
    holding the same values. (Registered as a ``collections.abc.Mapping``.)"""

    def _names(self) -> List[str]:
        return [f.name for f in fields(self)]  # type: ignore[arg-type]

    def to_dict(self) -> Dict[str, Any]:
        return {n: copy.deepcopy(getattr(self, n)) for n in self._names()}

    def keys(self) -> List[str]:
        return self._names()

    def values(self) -> List[Any]:
        return [getattr(self, n) for n in self._names()]

    def items(self) -> List[Tuple[str, Any]]:
        return [(n, getattr(self, n)) for n in self._names()]

    def __iter__(self) -> Iterator[str]:
        return iter(self._names())

    def __len__(self) -> int:
        return len(self._names())

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key) if key in self._names() else default

    def __getitem__(self, key: str) -> Any:
        if key not in self._names():
            raise KeyError(key)
        return getattr(self, key)

    def __contains__(self, key: object) -> bool:
        return key in self._names()

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _Section):
            return type(self) is type(other) and self.to_dict() == other.to_dict()
        if isinstance(other, Mapping):
            return self.to_dict() == dict(other)
        return NotImplemented

    __hash__ = None  # type: ignore[assignment]  # mutable settings are not hashable

    @classmethod
    def coerce(cls, value: Any) -> Any:
        """A mapping (e.g. from hand-built test data) -> this dataclass; known keys only."""
        if isinstance(value, cls):
            return value
        if value is None:
            return cls()
        if isinstance(value, Mapping):
            known = {f.name for f in fields(cls)}  # type: ignore[arg-type]
            return cls(**{k: copy.deepcopy(v) for k, v in value.items() if k in known})
        raise TypeError(f"expected a mapping for {cls.__name__}, got {type(value).__name__}")


collections.abc.Mapping.register(_Section)


@dataclass(eq=False)
class SizeRange(_Section):
    """``company_size``: head-count range (None = no limit on that side)."""

    min: Optional[int] = None
    max: Optional[int] = None


@dataclass(eq=False)
class Exclusions(_Section):
    """``exclusions``: the client's own do-not-list."""

    companies: List[str] = field(default_factory=list)   # plain names -> client suppression
    domains: List[str] = field(default_factory=list)     # -> icp.exclude_domains (added)
    keywords: List[str] = field(default_factory=list)    # -> icp.exclude_keywords (added)


@dataclass(eq=False)
class EmailPolicy(_Section):
    """``emails``: which email addresses may be delivered."""

    include_unverified: bool = True


@dataclass(eq=False)
class OpeningLine(_Section):
    """``opening_line``: the optional "Suggested opening line" column."""

    enabled: bool = False
    ai: bool = False
    max_cost_usd: float = 0.50


@dataclass(eq=False)
class Budget(_Section):
    """``budget``: per-run cap on paid API requests (0 = no extra cap)."""

    max_paid_lookups: int = 0


def _default_google_sheet() -> Dict[str, Any]:
    return {"spreadsheet_id": "", "worksheet": "{date}", "service_account_file": ""}


@dataclass(eq=False)
class DeliverySettings(_Section):
    """``delivery``: output formats, folder and the optional Google Sheets push."""

    formats: List[str] = field(default_factory=lambda: list(DELIVERY_FORMATS))
    folder: str = "deliveries/{client}/{date}"
    google_sheet: Dict[str, Any] = field(default_factory=_default_google_sheet)


# --- the client ----------------------------------------------------------------------

@dataclass
class Client:
    """One client of the Hiring Signal Report (a validated ``clients/<name>.yaml``)."""

    name: str                                   # file stem, e.g. "acme"
    path: Optional[Path] = None                 # the client file (None when built in code)
    display_name: str = ""                      # "" -> name
    playbook: str = DEFAULT_PLAYBOOK
    contact: Dict[str, Any] = field(default_factory=dict)
    roles: List[str] = field(default_factory=list)
    exclude_roles: List[str] = field(default_factory=list)
    buyer_titles: List[str] = field(default_factory=list)
    locations: List[str] = field(default_factory=list)
    exclude_locations: List[str] = field(default_factory=list)
    company_size: SizeRange = field(default_factory=SizeRange)
    industries: List[str] = field(default_factory=list)
    exclusions: Exclusions = field(default_factory=Exclusions)
    leads_per_week: int = 25
    freshness_days: int = 7
    allow_undated: bool = False
    drop_reposts: bool = True
    redelivery_days: Optional[int] = None       # None = never deliver the same item twice
    dedupe: List[str] = field(default_factory=lambda: list(DEDUPE_KINDS))
    tiers: List[str] = field(default_factory=lambda: ["hot", "normal"])
    emails: EmailPolicy = field(default_factory=EmailPolicy)
    opening_line: OpeningLine = field(default_factory=OpeningLine)
    budget: Budget = field(default_factory=Budget)
    delivery: DeliverySettings = field(default_factory=DeliverySettings)
    branding: Dict[str, Any] = field(default_factory=dict)
    overrides: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.name = str(self.name or "").strip()
        if self.path is not None and not isinstance(self.path, Path):
            self.path = Path(self.path)
        if not self.display_name:
            self.display_name = self.name
        # groups given as plain dicts (hand-built clients) become their dataclasses
        self.company_size = SizeRange.coerce(self.company_size)
        self.exclusions = Exclusions.coerce(self.exclusions)
        self.emails = EmailPolicy.coerce(self.emails)
        self.opening_line = OpeningLine.coerce(self.opening_line)
        self.budget = Budget.coerce(self.budget)
        self.delivery = DeliverySettings.coerce(self.delivery)

    @property
    def email_policy_include_unverified(self) -> bool:
        """True when risky / guessed emails may be delivered (not only "verified")."""
        return bool(self.emails.include_unverified)

    @property
    def excluded_companies(self) -> List[str]:
        """Company names on the client's own do-not-list (``exclusions.companies``)."""
        return list(self.exclusions.companies)

    @property
    def where(self) -> str:
        """How error messages name this client: its file path, else its name."""
        return str(self.path) if self.path else f"client '{self.name}'"

    def delivery_folder(self, day: date) -> Path:
        """``delivery.folder`` with ``{client}`` and ``{date}`` (YYYY-MM-DD) filled in."""
        try:
            return Path(str(self.delivery.folder).format(client=self.name, date=day.isoformat()))
        except (KeyError, IndexError, ValueError) as e:
            raise ClientError(f"{self.where}: delivery.folder {self.delivery.folder!r} has an unknown "
                              f"placeholder ({e}); use only {{client}} and {{date}}") from None

    def worksheet_name(self, day: date) -> str:
        """``delivery.google_sheet.worksheet`` with ``{client}`` / ``{date}`` filled in."""
        raw = str((self.delivery.google_sheet or {}).get("worksheet") or "{date}")
        try:
            return raw.format(client=self.name, date=day.isoformat())
        except (KeyError, IndexError, ValueError):
            return raw


# --- validation helpers ----------------------------------------------------------------

def _kind(value: Any) -> str:
    """Plain-English name of a YAML value's type (for error messages)."""
    if value is None:
        return "empty"
    if isinstance(value, bool):
        return "true/false"
    if isinstance(value, (int, float)):
        return "a number"
    if isinstance(value, str):
        return "text"
    if isinstance(value, (list, tuple)):
        return "a list"
    if isinstance(value, dict):
        return "a group of settings"
    return type(value).__name__


def _suggest(word: str, choices: Iterable[str]) -> str:
    match = difflib.get_close_matches(str(word), list(choices), n=1, cutoff=0.6)
    return match[0] if match else ""


def _unknown(label: str, key: Any, allowed: Sequence[str]) -> str:
    where = f"{label}." if label else ""
    guess = _suggest(str(key), allowed)
    if guess:
        return f"unknown setting '{where}{key}' - did you mean '{where}{guess}'?"
    return (f"unknown setting '{where}{key}' - check the spelling (allowed: {', '.join(allowed)}; "
            f"clients/_template.yaml explains each one)")


def _text(value: Any, label: str, errors: List[str], default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        errors.append(f"{label} must be text (got {_kind(value)}: {value!r})")
        return default
    return str(value).strip()


def _text_list(value: Any, label: str, errors: List[str]) -> List[str]:
    """A list of text items (a single text value counts as a one-item list), de-duplicated."""
    if value is None:
        return []
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        value = [value]
    if not isinstance(value, (list, tuple)):
        errors.append(f"{label} must be a list, e.g. [first, second] (got {_kind(value)})")
        return []
    out: List[str] = []
    seen = set()
    for i, item in enumerate(value, 1):
        if item is None or (isinstance(item, str) and not item.strip()):
            errors.append(f"{label}: item {i} is empty - remove it or fill it in")
            continue
        if isinstance(item, bool) or not isinstance(item, (str, int, float)):
            errors.append(f"{label}: item {i} must be text (got {_kind(item)}: {item!r})")
            continue
        text = str(item).strip()
        if text.lower() not in seen:
            seen.add(text.lower())
            out.append(text)
    return out


def _choice_list(value: Any, label: str, choices: Sequence[str], errors: List[str],
                 hints: Optional[Dict[str, str]] = None) -> List[str]:
    out: List[str] = []
    for item in _text_list(value, label, errors):
        v = item.lower()
        if v in choices:
            if v not in out:
                out.append(v)
            continue
        guess = (hints or {}).get(v) or _suggest(v, choices)
        tip = f" - did you mean '{guess}'?" if guess else ""
        errors.append(f"{label}: '{item}' is not allowed{tip} (choose from: {', '.join(choices)})")
    return out


def _flag(value: Any, label: str, default: bool, errors: List[str]) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    errors.append(f"{label} must be true or false (got {value!r})")
    return default


def _whole(value: Any, label: str, default: Optional[int], minimum: int, errors: List[str],
           extra: str = "") -> Optional[int]:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        bound = "> 0" if minimum == 1 else f">= {minimum}"
        errors.append(f"{label} must be a whole number {bound}{extra} (got {value!r})")
        return default
    return value


def _money(value: Any, label: str, default: float, errors: List[str]) -> float:
    if value is None:
        return default
    amount: Optional[float] = None
    if not isinstance(value, bool) and isinstance(value, (int, float)):
        try:
            amount = float(value)
        except OverflowError:   # an integer too big for a float
            amount = None
    # NaN / infinity (YAML .nan / .inf) would switch the cost cap off without saying so
    if amount is None or not math.isfinite(amount) or amount < 0:
        errors.append(f"{label} must be an amount in US dollars >= 0, e.g. 0.50 (got {value!r})")
        return default
    return amount


def _group(value: Any, label: str, allowed: Optional[Sequence[str]], errors: List[str]) -> Dict[str, Any]:
    """A settings group (mapping). ``allowed`` None = any keys; unknown keys are errors."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        errors.append(f"{label} must be a group of settings (indented 'key: value' lines), "
                      f"not {_kind(value)}")
        return {}
    out: Dict[str, Any] = {}
    for k, v in value.items():
        key = str(k)
        if allowed is not None and key not in allowed:
            errors.append(_unknown(label, key, allowed))
            continue
        out[key] = v
    return out


def _placeholders(text: str, label: str, errors: List[str]) -> None:
    try:
        names = [f for _, f, _, _ in string.Formatter().parse(text) if f is not None]
    except ValueError as e:
        errors.append(f"{label}: {text!r} has unbalanced braces ({e}); placeholders look like {{client}}")
        return
    for n in names:
        if n not in FOLDER_PLACEHOLDERS:
            errors.append(f"{label}: unknown placeholder {{{n}}} in {text!r} - use only {{client}} and {{date}}")


def _domains(value: Any, label: str, errors: List[str]) -> List[str]:
    out: List[str] = []
    for item in _text_list(value, label, errors):
        d = normalize_domain(item)
        if not d or not _DOMAIN_RE.match(d):
            errors.append(f"{label}: {item!r} is not a website domain (write it like bigclient.com)")
        elif d not in out:
            out.append(d)
    return out


def looks_like_domain(value: str) -> bool:
    """True for 'acme.com' / 'https://www.acme.com/' (not for 'Acme Inc')."""
    v = str(value or "").strip()
    if not v or " " in v:
        return False
    return bool(_DOMAIN_RE.match(normalize_domain(v)))


# --- parse + validate ----------------------------------------------------------------

def parse_client(data: Any, name: str, path: Optional[Path] = None) -> Client:
    """Validate the parsed YAML of a client file and build the ``Client``.

    Raises ``ClientError`` listing every problem found (not just the first).
    """
    where = str(path) if path else f"client '{name}'"
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ClientError(f"{where}: a client file must be a list of 'key: value' settings "
                          f"(see clients/_template.yaml), not {_kind(data)}")
    errors: List[str] = []
    for key in data:
        if str(key) not in CLIENT_KEYS:
            errors.append(_unknown("", key, CLIENT_KEYS))

    display_name = _text(data.get("display_name"), "display_name", errors) or name
    playbook = DEFAULT_PLAYBOOK
    if "playbook" in data:
        playbook = _text(data.get("playbook"), "playbook", errors)
        if not playbook:
            errors.append(f"playbook is empty - give the base playbook file, e.g. {DEFAULT_PLAYBOOK}")
            playbook = DEFAULT_PLAYBOOK
    contact = _group(data.get("contact"), "contact", None, errors)

    roles = _text_list(data.get("roles"), "roles", errors)
    exclude_roles = _text_list(data.get("exclude_roles"), "exclude_roles", errors)
    buyer_titles = _text_list(data.get("buyer_titles"), "buyer_titles", errors)
    locations = _text_list(data.get("locations"), "locations", errors)
    exclude_locations = _text_list(data.get("exclude_locations"), "exclude_locations", errors)
    industries = _text_list(data.get("industries"), "industries", errors)

    size = _group(data.get("company_size"), "company_size", ("min", "max"), errors)
    lo = _whole(size.get("min"), "company_size.min", None, 0, errors, " or empty")
    hi = _whole(size.get("max"), "company_size.max", None, 1, errors, " or empty")
    if lo is not None and hi is not None and lo > hi:
        errors.append(f"company_size.min ({lo}) must not be bigger than company_size.max ({hi})")

    excl = _group(data.get("exclusions"), "exclusions", ("companies", "domains", "keywords"), errors)
    exclusions = Exclusions(
        companies=_text_list(excl.get("companies"), "exclusions.companies", errors),
        domains=_domains(excl.get("domains"), "exclusions.domains", errors),
        keywords=_text_list(excl.get("keywords"), "exclusions.keywords", errors),
    )

    leads_per_week = _whole(data.get("leads_per_week"), "leads_per_week", 25, 1, errors)
    freshness_days = _whole(data.get("freshness_days"), "freshness_days", 7, 1, errors)
    allow_undated = _flag(data.get("allow_undated"), "allow_undated", False, errors)
    drop_reposts = _flag(data.get("drop_reposts"), "drop_reposts", True, errors)
    redelivery_days = data.get("redelivery_days")
    if redelivery_days is not None and (isinstance(redelivery_days, bool) or not isinstance(redelivery_days, int)
                                        or redelivery_days < 1):
        errors.append(f"redelivery_days must be empty (= never deliver the same thing twice) or a whole "
                      f"number of days > 0 (got {redelivery_days!r}); to switch de-duplication off "
                      f"use dedupe: []")
        redelivery_days = None
    dedupe = (list(DEDUPE_KINDS) if data.get("dedupe") is None
              else _choice_list(data.get("dedupe"), "dedupe", DEDUPE_KINDS, errors))
    tiers = ["hot", "normal"]
    if data.get("tiers") is not None:
        tiers = _choice_list(data.get("tiers"), "tiers", TIER_NAMES, errors)
        if not tiers and not any(e.startswith("tiers") for e in errors):
            errors.append("tiers is empty - list at least one of: hot, normal, skip")

    em = _group(data.get("emails"), "emails", ("include_unverified",), errors)
    emails = EmailPolicy(include_unverified=_flag(em.get("include_unverified"),
                                                  "emails.include_unverified", True, errors))

    ol = _group(data.get("opening_line"), "opening_line", ("enabled", "ai", "max_cost_usd"), errors)
    opening_line = OpeningLine(
        enabled=_flag(ol.get("enabled"), "opening_line.enabled", False, errors),
        ai=_flag(ol.get("ai"), "opening_line.ai", False, errors),
        max_cost_usd=_money(ol.get("max_cost_usd"), "opening_line.max_cost_usd", 0.50, errors),
    )

    bud = _group(data.get("budget"), "budget", ("max_paid_lookups",), errors)
    budget = Budget(max_paid_lookups=_whole(bud.get("max_paid_lookups"), "budget.max_paid_lookups",
                                            0, 0, errors, " (0 = no cap)") or 0)

    dl = _group(data.get("delivery"), "delivery", ("formats", "folder", "google_sheet"), errors)
    formats = list(DELIVERY_FORMATS)
    if dl.get("formats") is not None:
        formats = _choice_list(dl.get("formats"), "delivery.formats", DELIVERY_FORMATS, errors,
                               hints=_FORMAT_HINTS)
        if not formats and not any(e.startswith("delivery.formats") for e in errors):
            errors.append("delivery.formats is empty - list at least one of: csv, xlsx, html")
    folder = _text(dl.get("folder"), "delivery.folder", errors) or "deliveries/{client}/{date}"
    _placeholders(folder, "delivery.folder", errors)
    gs_raw = _group(dl.get("google_sheet"), "delivery.google_sheet", GOOGLE_SHEET_KEYS, errors)
    google_sheet = _default_google_sheet()
    for k, v in gs_raw.items():
        google_sheet[k] = _text(v, f"delivery.google_sheet.{k}", errors)
    google_sheet["worksheet"] = google_sheet.get("worksheet") or "{date}"
    _placeholders(google_sheet["worksheet"], "delivery.google_sheet.worksheet", errors)

    branding = _group(data.get("branding"), "branding", BRANDING_KEYS, errors)
    for k, v in list(branding.items()):
        branding[k] = _text(v, f"branding.{k}", errors)
    color = branding.get("brand_color")
    if color and not _HEX_COLOR_RE.match(color):
        errors.append(f"branding.brand_color must be a hex colour like #1f4e79 (got {color!r})")

    overrides = _group(data.get("overrides"), "overrides", None, errors)
    for k in list(overrides):
        if k == "mode":
            errors.append("overrides.mode can't be set: client deliveries always run in delivery mode")
            overrides.pop(k)
        elif k == "name":
            errors.append("overrides.name can't be set: the playbook name comes from the client file "
                          "name (client-<name>)")
            overrides.pop(k)
        elif k not in OVERRIDE_SECTIONS:
            errors.append(_unknown("overrides", k, OVERRIDE_SECTIONS))
            overrides.pop(k)
        elif k == "sources" and not isinstance(overrides[k], list):
            errors.append(f"overrides.sources must be a list of sources (got {_kind(overrides[k])})")
        elif k == "description" and not isinstance(overrides[k], str):
            errors.append("overrides.description must be text")
        elif k not in ("sources", "description") and not isinstance(overrides[k], dict):
            errors.append(f"overrides.{k} must be a group of settings, like the '{k}:' section "
                          f"of a playbook (got {_kind(overrides[k])})")

    if errors:
        if len(errors) == 1:
            raise ClientError(f"{where}: {errors[0]}")
        raise ClientError(f"{where} has {len(errors)} problems:\n  - " + "\n  - ".join(errors))

    return Client(
        name=name, path=path, display_name=display_name, playbook=playbook,
        contact=copy.deepcopy(contact), roles=roles, exclude_roles=exclude_roles,
        buyer_titles=buyer_titles, locations=locations, exclude_locations=exclude_locations,
        company_size=SizeRange(min=lo, max=hi), industries=industries, exclusions=exclusions,
        leads_per_week=int(leads_per_week or 25), freshness_days=int(freshness_days or 7),
        allow_undated=allow_undated, drop_reposts=drop_reposts, redelivery_days=redelivery_days,
        dedupe=dedupe, tiers=tiers, emails=emails, opening_line=opening_line, budget=budget,
        delivery=DeliverySettings(formats=formats, folder=folder, google_sheet=google_sheet),
        branding=branding, overrides=copy.deepcopy(overrides),
    )


# --- finding + loading client files ----------------------------------------------------

def list_clients(clients_dir: str = DEFAULT_CLIENTS_DIR) -> List[str]:
    """Client names (file stems) in ``clients_dir``, sorted. Files starting with
    ``_`` (the template) or ``.`` are skipped; a missing folder gives []."""
    folder = Path(clients_dir)
    if not folder.is_dir():
        return []
    names = set()
    for p in folder.iterdir():
        if p.is_file() and p.suffix.lower() in (".yaml", ".yml") and not p.name.startswith(("_", ".")):
            names.add(p.stem)
    return sorted(names)


def _as_on_disk(path: Path) -> Path:
    """``path`` with its file name spelled as it is stored. On case-insensitive disks
    (macOS, Windows) ``clients/Acme.yaml`` opens ``clients/acme.yaml``; the client name
    (the ledger's key for its history and do-not-list) must come from the real file."""
    try:
        names = os.listdir(path.parent)
    except OSError:
        return path
    if path.name in names:
        return path
    same = [n for n in names if n.casefold() == path.name.casefold()]
    return path.with_name(same[0]) if len(same) == 1 else path


def _locate(name_or_path: str, clients_dir: str) -> Path:
    raw = str(name_or_path or "").strip()
    if not raw:
        raise ClientError("no client given - use a client name such as 'acme' (see: leadgen clients list)")
    p = Path(os.path.expanduser(raw))
    folder = Path(clients_dir)
    is_yaml = p.suffix.lower() in (".yaml", ".yml")
    candidates: List[Path] = []
    if p.is_absolute() or p.parent != Path("."):
        candidates.append(p)
        if not is_yaml:
            candidates += [p.with_name(p.name + ".yaml"), p.with_name(p.name + ".yml")]
    elif is_yaml:
        candidates += [folder / p.name, p]
    else:
        candidates += [folder / f"{raw}.yaml", folder / f"{raw}.yml", folder / raw]
    for c in candidates:
        if c.is_file():
            return _as_on_disk(c)
    known = list_clients(clients_dir)
    stem = p.stem if is_yaml else p.name
    msg = f"client '{raw}' not found: there is no {candidates[0]}."
    guess = _suggest(stem, known)
    if guess:
        msg += f" Did you mean '{guess}'?"
    if known:
        msg += f" Clients in {folder}/: {', '.join(known)}."
    else:
        msg += f" There are no client files in {folder}/ yet."
    if CLIENT_NAME_RE.match(stem):
        msg += f" Create it with: leadgen clients new {stem}"
    raise ClientError(msg)


def load_client(name_or_path: str, clients_dir: str = DEFAULT_CLIENTS_DIR) -> Client:
    """Load and validate a client: ``acme``, ``acme.yaml`` (both looked up in
    ``clients_dir``) or a path to the file. The client's name is the file's stem as
    stored on disk (``Acme`` on a case-insensitive disk still loads client ``acme``).
    Raises ``ClientError``."""
    path = _locate(name_or_path, clients_dir)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        raise ClientError(f"{path}: could not read the file ({e})") from e
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ClientError(f"{path}: this is not valid YAML ({e}). Check the indentation, and put "
                          f"text containing ':' or '#' in quotes.") from e
    return parse_client(data, name=path.stem, path=path)


# --- the client's playbook -------------------------------------------------------------

def playbook_name_for(client_name: str) -> str:
    """The playbook name used for a client: ``client-<name>`` made safe
    (letters, digits, '-', '_' and '.')."""
    s = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(client_name or "").strip()).lower()
    s = re.sub(r"-{2,}", "-", s).strip("-.")
    return f"client-{s}" if s else "client"


def _deep_merge(base: Dict[str, Any], override: Mapping[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, Mapping) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _section(data: Dict[str, Any], key: str) -> Dict[str, Any]:
    cur = data.get(key)
    if not isinstance(cur, dict):
        cur = {}
        data[key] = cur
    return cur


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _merged(first: Any, extra: Iterable[str], norm: Any = None) -> List[Any]:
    """``first`` + the items of ``extra`` not already in it (compared case-insensitively
    or with ``norm``)."""
    norm = norm or (lambda x: str(x).strip().lower())
    out = list(_as_list(first))
    seen = {norm(x) for x in out}
    for item in extra:
        if norm(item) not in seen:
            seen.add(norm(item))
            out.append(item)
    return out


def is_handover_exporter(type_name: str) -> bool:
    """True when the exporter type hands leads to a sending tool (class ``scope ==
    "outbound"``). Checked on the class, without building the adapter; unknown
    types or types that fail to import count as review exporters (the pipeline
    reports them when it builds them)."""
    try:
        cls = registry.resolve("exporter", str(type_name))
    except Exception:  # noqa: BLE001 - unknown type / missing optional dependency
        return False
    return getattr(cls, "scope", "all") == "outbound"


def find_base_playbook(client: Client) -> Path:
    """Where ``client.playbook`` lives: as given (absolute, or relative to the
    current folder), then next to the client file, then one folder above it."""
    raw = Path(os.path.expanduser(str(client.playbook or DEFAULT_PLAYBOOK)))
    candidates: List[Path] = [raw]
    if not raw.is_absolute() and client.path is not None:
        candidates += [client.path.parent / raw, client.path.parent.parent / raw]
    unique: List[Path] = []
    for c in candidates:
        if c not in unique:
            unique.append(c)
    for c in unique:
        if c.is_file():
            return c
    looked = ", ".join(str(c) for c in unique)
    raise ClientError(f"{client.where}: base playbook '{client.playbook}' not found (looked for: {looked}). "
                      f"Fix the 'playbook:' line in the client file.")


def client_playbook(client: Client, env: Optional[Dict[str, str]] = None) -> Playbook:
    """The playbook to run for ``client``: its base playbook with the client's
    targeting applied (see the module docstring for the mapping), validated.

    The returned playbook keeps the base playbook's ``path``, so relative file
    paths inside it (CSV sources, contact lists) still resolve against the base
    playbook's folder. Raises ``ClientError``.
    """
    base_path = find_base_playbook(client)
    try:
        base = load_playbook(str(base_path), env=env)
    except PlaybookError as e:
        raise ClientError(f"{client.where}: the base playbook {base_path} can't be used: {e}") from e

    data: Dict[str, Any] = copy.deepcopy(base.raw) if isinstance(base.raw, dict) else {}

    sig = _section(data, "signals")
    if client.roles:
        sig["match_keywords"] = list(client.roles)
    sig["exclude_keywords"] = _merged(base.signals.get("exclude_keywords"), client.exclude_roles)
    sig["max_age_days"] = int(client.freshness_days)
    sig["allow_undated"] = bool(client.allow_undated)
    sig["drop_reposts"] = bool(client.drop_reposts)
    # sources with their own date window must look back at least freshness_days
    # (widen only, never narrow: the signals stage applies the exact window)
    windows = {"adzuna": "max_days_old", "theirstack": "max_age_days"}
    for src in data.get("sources") or []:
        key = windows.get(str(src.get("type"))) if isinstance(src, dict) else None
        if key:
            try:
                current = int(src.get(key) or 0)
            except (TypeError, ValueError):
                current = 0
            if current and current < int(client.freshness_days):
                src[key] = int(client.freshness_days)

    buyers = _section(data, "buyers")
    if client.buyer_titles:
        buyers["titles"] = list(client.buyer_titles)
    buyers["max_contacts_per_company"] = 1

    icp = _section(data, "icp")
    icp["locations"] = list(client.locations)
    icp["exclude_locations"] = list(client.exclude_locations)
    icp["employees"] = {"min": client.company_size.min, "max": client.company_size.max}
    icp["industries"] = list(client.industries)
    icp["exclude_domains"] = _merged(base.icp.get("exclude_domains"), client.exclusions.domains,
                                     norm=normalize_domain)
    icp["exclude_keywords"] = _merged(base.icp.get("exclude_keywords"), client.exclusions.keywords)

    # enrichment credit guard: enough companies to fill the file, not the base's 200
    enr = _section(data, "enrichment")
    target = max(int(client.leads_per_week) * 2, 10)
    raw_enr = base.raw.get("enrichment") if isinstance(base.raw, dict) else None
    base_cap = to_int(base.enrichment.get("max_companies")) if (
        isinstance(raw_enr, dict) and "max_companies" in raw_enr) else None
    enr["max_companies"] = min(target, base_cap) if base_cap and base_cap > 0 else target

    if client.budget.max_paid_lookups > 0:
        _section(data, "usage")["max_paid_lookups"] = int(client.budget.max_paid_lookups)

    if client.branding:
        data["delivery"] = _deep_merge(_section(data, "delivery"), client.branding)

    data["description"] = f"Hiring Signal Report for {client.display_name} (base playbook: {base_path})"

    overrides = {k: v for k, v in (client.overrides or {}).items() if k not in ("name", "mode")}
    if overrides:
        data = _deep_merge(data, overrides)

    out = _section(data, "outbound")
    exporters = out["exporters"] if "exporters" in out else base.outbound.get("exporters")
    out["exporters"] = [e for e in _as_list(exporters)
                        if not (isinstance(e, dict) and is_handover_exporter(str(e.get("type"))))]

    data["mode"] = "delivery"
    data["name"] = playbook_name_for(client.name)
    try:
        return from_dict(data, path=base.path, env=env)
    except PlaybookError as e:
        # "invalid playbook (<base path>):\n  - problem" -> just the problem lines: the
        # problems come from the base playbook + this client file together
        lines = str(e).splitlines()
        if len(lines) > 1 and lines[0].startswith("invalid playbook"):
            detail = "\n" + "\n".join(lines[1:])
        else:
            detail = f" {e}"
        raise ClientError(f"{client.where}: the settings built for this client (base playbook "
                          f"{base_path} + the client file) are not valid:{detail}") from e


# --- new client files ------------------------------------------------------------------

def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9_-]+", "-", str(name or "").strip().lower())
    s = re.sub(r"-{2,}", "-", s).strip("-_")
    return s or "my-client"


def _find_template(template: str, folder: Path) -> Path:
    candidates = [Path(os.path.expanduser(str(template)))]
    if str(template) == DEFAULT_TEMPLATE:
        packaged = Path(__file__).resolve().parents[2] / "clients" / "_template.yaml"
        candidates += [folder / "_template.yaml", packaged]
    for c in candidates:
        if c.is_file():
            return c
    raise ClientError(f"client template not found: {template} - restore clients/_template.yaml "
                      f"from the repository, or pass another template file")


def new_client_file(name: str, clients_dir: str = DEFAULT_CLIENTS_DIR,
                    template: str = DEFAULT_TEMPLATE) -> Path:
    """Create ``<clients_dir>/<name>.yaml`` as a copy of the template and return its path.

    ``name`` must be lower-case letters, digits, '-' and '_' (starting with a
    letter or digit). An existing client file is never overwritten.
    """
    raw = str(name or "").strip()
    if not CLIENT_NAME_RE.match(raw):
        raise ClientError(f"client name {raw!r} is not allowed: use lower-case letters, digits, '-' and "
                          f"'_', starting with a letter or digit (for example '{_slug(raw)}')")
    folder = Path(clients_dir)
    target = folder / f"{raw}.yaml"
    for existing in (target, folder / f"{raw}.yml"):
        if existing.exists():
            raise ClientError(f"{existing} already exists - edit that file, or choose another name")
    src = _find_template(template, folder)
    text = src.read_text(encoding="utf-8")
    folder.mkdir(parents=True, exist_ok=True)
    try:
        with open(target, "x", encoding="utf-8") as f:  # "x": never overwrite, even in a race
            f.write(text)
    except FileExistsError:
        raise ClientError(f"{target} already exists - edit that file, or choose another name") from None
    return target
