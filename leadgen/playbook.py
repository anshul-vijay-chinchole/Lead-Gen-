"""Playbooks: the per-niche "recipe card" that drives the whole engine.

A playbook is a YAML file. Nothing in the engine is niche-specific; everything
that changes between niches (signals, ideal customer, buyer titles, angle,
tools) lives here. ``load_playbook`` merges the file over ``DEFAULTS`` so every
section always exists, expands ``${ENV_VAR}`` / ``${ENV_VAR:-default}``
references, and validates the result.

Sections (all optional except ``name``):

    name, description
    offer:      who we are, what we sell, CTA, booking link, sender identity
    icp:        the ideal customer (locations, size, industries, keywords, exclusions)
    signals:    which "why now" signals count and how fresh they must be
    buyers:     which job titles to contact, in priority order
    sources:    list of source adapters to pull companies/signals from
    enrichment: contact-finder waterfall + verifier
    scoring:    weights + tier thresholds
    writer:     AI / template copywriting + sequence + guardrails
    outbound:   exporters / senders (CSV, Instantly, Smartlead, ...)
    replies:    reply classifier settings
    notify:     where alerts go (Slack, webhook, console)
    storage:    SQLite path
"""
from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class PlaybookError(ValueError):
    """Raised when a playbook is missing or invalid."""


DEFAULTS: Dict[str, Any] = {
    "name": "",
    "description": "",
    "offer": {
        "sender_name": "",
        "sender_title": "",
        "sender_company": "",
        "sender_website": "",
        "service": "",            # what we do, one line
        "value_prop": "",         # the outcome we deliver
        "proof": "",              # a credibility line (results, clients, ...)
        "cta": "Worth a quick chat?",
        "booking_link": "",
        "signature": "",
        "footer": "",             # optional line appended to every email (e.g. opt-out text)
        "language": "English",
    },
    "icp": {
        "locations": [],          # any match (substring, case-insensitive) in company location/country
        "exclude_locations": [],
        "employees": {"min": None, "max": None},
        "industries": [],         # any match in industry/keywords/description
        "exclude_industries": [],
        "keywords": [],           # any match in name/description/keywords/signals
        "exclude_keywords": [],
        "exclude_domains": [],
        "exclude_company_patterns": [],  # regexes on company name
        "require_domain": False,
        "unknown_passes": True,   # company with missing data (size/location/industry) passes that check
    },
    "signals": {
        "types": [],              # accepted signal types; [] = all
        "primary": ["job_posting"],  # types that drive the intent score; others are "extra"
        "require": True,          # drop companies with no accepted signal
        "match_keywords": [],     # signal title must contain one (e.g. job titles you recruit for)
        "exclude_keywords": [],   # drop signals whose title contains one (e.g. "intern")
        "max_age_days": 60,
        "urgency_keywords": ["urgent", "immediate", "immediately", "asap", "start now", "quick start"],
        "stale_after_days": 21,   # a signal still open this long = hard-to-fill / persistent need
        "match_description": True,  # match_keywords may also hit the signal description
    },
    "buyers": {
        "titles": [],             # priority order: first = best
        "exclude_titles": ["intern", "assistant", "student", "trainee"],
        "seniorities": [],        # provider-specific filter hints (e.g. apollo: ["vp","director","c_suite"])
        "departments": [],
        "max_contacts_per_company": 1,
        "allow_generic_emails": False,  # info@, hr@, jobs@ ...
    },
    "sources": [],
    "enrichment": {
        "finders": [],            # e.g. [{type: apollo}, {type: hunter}, {type: pattern}]
        "verifier": {"type": "basic"},
        "accept_statuses": ["valid", "risky"],
        "max_companies": 200,     # enrichment credit guard: only the top-N pre-scored companies
        "min_prescore": 0,        # skip enrichment for companies pre-scoring below this
        "skip_if_contact_present": True,
    },
    "scoring": {
        "weights": {"intent": 40, "fit": 30, "reachability": 20, "extra": 10},
        "tiers": {"hot": 80, "normal": 60},
        "intent": {"fresh": 15, "volume": 10, "persistence": 10, "urgency": 5},
        "freshness_days": [3, 7, 14, 30],  # full / 3/4 / 1/2 / 1/4 credit bands
        "volume_full_at": 3,      # this many relevant signals = full volume points
        "extra_per_signal": 5,
        "unknown_credit": 0.5,    # share of fit points given when company data is missing
    },
    "writer": {
        "type": "template",       # "ai" or "template"
        "provider": "",           # ai: openai | anthropic | openai_compatible
        "model": "",
        "base_url": "",
        "api_key_env": "",
        "temperature": None,
        "tone": "friendly, direct, peer-to-peer; no hype",
        "max_words": 90,
        "sequence": [
            {"day": 1, "purpose": "opener: why them, why now, soft CTA"},
            {"day": 3, "purpose": "short bump adding one new angle or proof point"},
            {"day": 7, "purpose": "value add: a relevant insight or offer"},
            {"day": 12, "purpose": "polite breakup, easy yes/no"},
        ],
        "banned_phrases": [
            "I hope this email finds you well", "leading provider", "world-class",
            "synergy", "revolutionize", "game-changer", "cutting-edge",
            "I wanted to reach out", "touch base", "circle back",
        ],
        "extra_instructions": "",
        # template writer overrides: {subject, subjects: [...], steps: [str | {subject, body}],
        #   signal_phrases: {type: str}, hypotheses: {type: str}, topics: {type: str}}
        "templates": {},
        "max_tokens": 4000,       # per-call output budget for the AI writer (reasoning models think inside it)
        "max_llm_failures": 3,    # consecutive LLM failures before the AI writer switches to templates
        "acronyms": [],           # extra ALL-CAPS words the guardrails accept (e.g. SOC2, HIPAA)
        "llm": {},                # extra client options: timeout, reasoning_effort, effort, extra_body, ...
        "fallback_to_template": True,
        "max_leads": 500,
        "tiers": ["hot", "normal"],
    },
    "outbound": {
        "exporters": [{"type": "csv"}],
        "tiers": ["hot", "normal"],
        "require_email": True,
        "dedupe_days": 90,        # don't export the same person again within N days
        # don't email a colleague at a company that was contacted in the last N days
        # (0 = off). Companies that replied, unsubscribed or said no are always skipped.
        "company_cooldown_days": 30,
    },
    "replies": {
        "classifier": "auto",     # auto (ai if configured else rules) | rules | ai
        "timing_default_days": 30,
        "ooo_default_days": 7,
    },
    "notify": {
        "channels": [{"type": "console"}],
        "on": ["positive", "referral", "question", "run_summary"],
    },
    "storage": {"path": "data/leadgen.db"},
}

_LIST_OF_DICT_SECTIONS = ("sources",)


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def expand_env(value: Any, env: Optional[Dict[str, str]] = None) -> Any:
    """Recursively replace ${VAR} and ${VAR:-default} in strings."""
    env = os.environ if env is None else env
    if isinstance(value, str):
        def repl(m: "re.Match[str]") -> str:
            return env.get(m.group(1)) or (m.group(2) if m.group(2) is not None else "")
        return _ENV_RE.sub(repl, value)
    if isinstance(value, list):
        return [expand_env(v, env) for v in value]
    if isinstance(value, dict):
        return {k: expand_env(v, env) for k, v in value.items()}
    return value


@dataclass
class Playbook:
    """Validated playbook. Sections are plain dicts (already merged with defaults)."""

    name: str
    description: str
    offer: Dict[str, Any]
    icp: Dict[str, Any]
    signals: Dict[str, Any]
    buyers: Dict[str, Any]
    sources: List[Dict[str, Any]]
    enrichment: Dict[str, Any]
    scoring: Dict[str, Any]
    writer: Dict[str, Any]
    outbound: Dict[str, Any]
    replies: Dict[str, Any]
    notify: Dict[str, Any]
    storage: Dict[str, Any]
    path: Optional[Path] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def base_dir(self) -> Path:
        """Relative paths in the playbook resolve against the current working dir
        first, then the playbook's own directory."""
        return self.path.parent if self.path else Path.cwd()

    def resolve_path(self, p: str) -> Path:
        path = Path(os.path.expanduser(p))
        if path.is_absolute() or path.exists():
            return path
        candidate = self.base_dir / path
        return candidate if candidate.exists() else path

    @property
    def db_path(self) -> Path:
        return Path(os.path.expanduser(self.storage.get("path") or "data/leadgen.db"))

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in DEFAULTS}


def _as_list(v: Any) -> List[Any]:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return list(v)
    return [v]


def validate(data: Dict[str, Any]) -> List[str]:
    """Return a list of human-readable problems (empty = valid)."""
    errs: List[str] = []
    if not data.get("name"):
        errs.append("'name' is required")
    elif not re.match(r"^[A-Za-z0-9_.-]+$", str(data["name"])):
        errs.append("'name' may only contain letters, digits, '-', '_' and '.'")
    for key in ("offer", "icp", "signals", "buyers", "enrichment", "scoring", "writer",
                "outbound", "replies", "notify", "storage"):
        if not isinstance(data.get(key), dict):
            errs.append(f"'{key}' must be a mapping")
    if not isinstance(data.get("sources"), list):
        errs.append("'sources' must be a list")
    else:
        for i, s in enumerate(data["sources"]):
            if not isinstance(s, dict) or not s.get("type"):
                errs.append(f"sources[{i}] must be a mapping with a 'type'")
    if errs:
        return errs

    emp = data["icp"].get("employees") or {}
    if not isinstance(emp, dict):
        errs.append("icp.employees must be a mapping with min/max")
    else:
        lo, hi = emp.get("min"), emp.get("max")
        for label, v in (("min", lo), ("max", hi)):
            if v is not None and not isinstance(v, int):
                errs.append(f"icp.employees.{label} must be an integer")
        if isinstance(lo, int) and isinstance(hi, int) and lo > hi:
            errs.append("icp.employees.min must be <= max")
    for pat in data["icp"].get("exclude_company_patterns") or []:
        try:
            re.compile(pat)
        except re.error as e:
            errs.append(f"icp.exclude_company_patterns: invalid regex {pat!r}: {e}")

    tiers = data["scoring"].get("tiers") or {}
    hot, normal = tiers.get("hot"), tiers.get("normal")
    if not (isinstance(hot, (int, float)) and isinstance(normal, (int, float))):
        errs.append("scoring.tiers.hot and scoring.tiers.normal must be numbers")
    elif not 0 <= normal <= hot <= 100:
        errs.append("scoring.tiers must satisfy 0 <= normal <= hot <= 100")
    weights = data["scoring"].get("weights") or {}
    for k in ("intent", "fit", "reachability", "extra"):
        if not isinstance(weights.get(k), (int, float)) or weights.get(k) < 0:
            errs.append(f"scoring.weights.{k} must be a non-negative number")

    w = data["writer"]
    if w.get("type") not in ("ai", "template"):
        errs.append("writer.type must be 'ai' or 'template'")
    if w.get("type") == "ai" and w.get("provider") not in ("openai", "anthropic", "openai_compatible"):
        errs.append("writer.provider must be openai, anthropic or openai_compatible when writer.type is 'ai'")
    seq = w.get("sequence")
    if not isinstance(seq, list) or not seq:
        errs.append("writer.sequence must be a non-empty list")
    else:
        days = []
        for i, step in enumerate(seq):
            if not isinstance(step, dict) or not isinstance(step.get("day"), int):
                errs.append(f"writer.sequence[{i}] needs an integer 'day'")
            else:
                days.append(step["day"])
        if days and days != sorted(days):
            errs.append("writer.sequence days must be in ascending order")
    if not isinstance(w.get("max_words"), int) or w["max_words"] < 20:
        errs.append("writer.max_words must be an integer >= 20")

    for i, e in enumerate(_as_list(data["enrichment"].get("finders"))):
        if not isinstance(e, dict) or not e.get("type"):
            errs.append(f"enrichment.finders[{i}] must be a mapping with a 'type'")
    ver = data["enrichment"].get("verifier")
    if ver is not None and (not isinstance(ver, dict) or not ver.get("type")):
        errs.append("enrichment.verifier must be a mapping with a 'type' (or null)")
    for i, e in enumerate(_as_list(data["outbound"].get("exporters"))):
        if not isinstance(e, dict) or not e.get("type"):
            errs.append(f"outbound.exporters[{i}] must be a mapping with a 'type'")
    for i, e in enumerate(_as_list(data["notify"].get("channels"))):
        if not isinstance(e, dict) or not e.get("type"):
            errs.append(f"notify.channels[{i}] must be a mapping with a 'type'")
    if data["replies"].get("classifier") not in ("auto", "rules", "ai"):
        errs.append("replies.classifier must be auto, rules or ai")
    return errs


def from_dict(data: Dict[str, Any], path: Optional[Path] = None,
              env: Optional[Dict[str, str]] = None) -> Playbook:
    if not isinstance(data, dict):
        raise PlaybookError("playbook must be a YAML mapping")
    merged = _deep_merge(DEFAULTS, expand_env(data, env))
    # list-valued sections replace defaults entirely (handled by _deep_merge), but
    # make sure None becomes an empty list/dict.
    for key, default in DEFAULTS.items():
        if merged.get(key) is None:
            merged[key] = copy.deepcopy(default)
    problems = validate(merged)
    if problems:
        where = f" ({path})" if path else ""
        raise PlaybookError(f"invalid playbook{where}:\n  - " + "\n  - ".join(problems))
    return Playbook(
        name=str(merged["name"]),
        description=str(merged.get("description") or ""),
        offer=merged["offer"],
        icp=merged["icp"],
        signals=merged["signals"],
        buyers=merged["buyers"],
        sources=merged["sources"],
        enrichment=merged["enrichment"],
        scoring=merged["scoring"],
        writer=merged["writer"],
        outbound=merged["outbound"],
        replies=merged["replies"],
        notify=merged["notify"],
        storage=merged["storage"],
        path=path,
        raw=data,
    )


def load_playbook(path: str, env: Optional[Dict[str, str]] = None) -> Playbook:
    p = Path(path)
    if not p.exists():
        raise PlaybookError(f"playbook not found: {path}")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise PlaybookError(f"could not parse {path}: {e}") from e
    return from_dict(data, path=p, env=env)
