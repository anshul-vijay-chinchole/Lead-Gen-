"""Stage 2 - SIGNALS: which "why now" signals count, and how strong they are.

``process_signals`` cleans every company's signal list according to the
playbook's ``signals`` section and drops companies that are left without a
live signal. ``signal_stats`` summarises what is left for the scorer.

It also hosts ``keyword_match`` - the one keyword matcher the whole engine
uses (ICP filter, buyer titles, urgency language, ...), so "does this text
mention X?" behaves the same everywhere.

Playbook keys read (section ``signals``):

``types``
    Accepted signal types (e.g. ``[job_posting, funding]``). Empty = all.
``primary``
    Types that drive the intent score (default ``[job_posting]``); every
    other accepted type is a *secondary* signal that only earns "extra"
    points.
``require``
    Reject a company when no signal survives the rules below.
``match_keywords``
    For **primary** signals only: the title (or, failing that, the
    description) must mention one of these (e.g. the roles you recruit for).
    Empty = no requirement.
``match_description``
    When false, ``match_keywords`` must hit the title itself; the description
    is not used as a fallback (default true).
``exclude_keywords``
    Drop any signal whose title mentions one of these (e.g. ``intern``).
``max_age_days``
    Drop signals older than this (``Signal.age_days``). ``null`` disables the
    age limit. Undated signals age from the day the engine first saw them
    (``Store.observe_signals`` sets ``first_seen``), so an undated signal that
    has never been seen before is kept.
``urgency_keywords``
    Words that mark a primary signal as urgent (``signal_stats['urgent']``).
``stale_after_days``
    A primary signal still open this long (or re-posted) is a persistent,
    hard-to-fill need (``signal_stats['persistent']``).
"""
from __future__ import annotations

import re
from collections import Counter
from functools import lru_cache
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

from .models import Company, Signal
from .utils import normalize_text, strip_accents, to_int

# Tokens shorter than this never get plural tolerance ("it" must not match "its").
MIN_PLURAL_LEN = 3
# Joined compounds shorter than this are not matched across token boundaries
# (keeps "e-commerce" ~ "ecommerce" without letting "u s" ~ "us").
MIN_COMPOUND_LEN = 5
# "-es" is only a plural ending after these (tax/taxes, church/churches, hero/heroes);
# elsewhere it makes another word (rat/rates, car/cares, not/notes).
_ES_STEMS = ("s", "x", "z", "ch", "sh", "o")
# Words that look like the plural of a shorter word but are not ("news" is not "new"s).
_NOT_PLURALS = frozenset({"news", "goods"})
# Common English words that are also acronyms or codes ("IT", "US", "IN", "OR").
# As a keyword they only match an ALL-CAPS word in the text (see keyword_match),
# so "IT" does not match "make it grow" and "US" does not match "contact us".
_ACRONYM_WORDS = frozenset({
    "it", "us", "in", "on", "or", "as", "at", "an", "am", "is", "be", "do", "go", "me",
    "my", "no", "so", "to", "up", "we", "he", "if", "by", "of", "ok", "hi", "id",
})
_CASE_SPLIT = re.compile(r"[^A-Za-z0-9]+")


# --- keyword matching -----------------------------------------------------------

def tokenize(text: Any) -> List[str]:
    """Lowercase, accent-free word tokens (punctuation splits words)."""
    return normalize_text(text).split()


@lru_cache(maxsize=8192)
def _needle_tokens(keyword: str) -> Tuple[str, ...]:
    return tuple(normalize_text(keyword).split())


def _case_tokens(text: Any) -> List[str]:
    """Like ``tokenize`` but keeping the original letter case."""
    return _CASE_SPLIT.sub(" ", strip_accents(str(text))).split()


@lru_cache(maxsize=8192)
def _strict_tokens(keyword: str) -> Tuple[bool, ...]:
    """Per keyword token: must it match an ALL-CAPS word in the text?

    True for an acronym-like word (``_ACRONYM_WORDS``) that is the whole
    keyword (``IT``, ``us``) or is written in capitals inside a mixed-case
    phrase (``IT`` in ``Head of IT``, not ``of`` in ``HEAD OF FINANCE``).
    """
    tokens = _needle_tokens(keyword)
    raw = _case_tokens(keyword)
    aligned = [r.lower() for r in raw] == list(tokens)
    all_caps = aligned and all(r.isupper() or not r.isalpha() for r in raw)
    return tuple(
        t in _ACRONYM_WORDS and (len(tokens) == 1 or (aligned and raw[i].isupper() and not all_caps))
        for i, t in enumerate(tokens))


def _upper_flags(text: Any, hay: Sequence[str]) -> Optional[List[bool]]:
    """Which ``hay`` tokens are ALL CAPS in the original ``text`` (None if they cannot be aligned)."""
    raw = _case_tokens(text)
    if len(raw) != len(hay) or any(r.lower() != h for r, h in zip(raw, hay)):
        return None
    return [r.isupper() for r in raw]


def as_str_list(value: Any) -> List[str]:
    """Coerce a config value (None, a string, a list of anything) to a clean list of strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(v) for v in value if v is not None and str(v).strip()]
    return [str(value)]


def tokens_equal(a: str, b: str) -> bool:
    """Token equality tolerant of simple English plurals (accountant ~ accountants,
    tax ~ taxes, company ~ companies). Tokens shorter than 3 chars must match exactly;
    "-es" only counts after s/x/z/ch/sh/o (rat !~ rates) and a few words that only
    look like plurals never match their stem (new !~ news)."""
    if a == b:
        return True
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    if len(short) < MIN_PLURAL_LEN or long_ in _NOT_PLURALS:
        return False
    if long_ == short + "s" or (long_ == short + "es" and short.endswith(_ES_STEMS)):
        return True
    if short.endswith("y") and long_ == short[:-1] + "ies":
        return True
    return False


def token_spans(hay: Sequence[str], needle: Sequence[str]) -> Iterator[Tuple[int, int]]:
    """Every ``(start, end)`` slice of ``hay`` where ``needle`` occurs (see ``contains_tokens``)."""
    k, n = len(needle), len(hay)
    if k == 0 or n == 0:
        return
    for i in range(n - k + 1):
        if all(tokens_equal(hay[i + j], needle[j]) for j in range(k)):
            yield i, i + k
    joined = "".join(needle)
    if len(joined) < MIN_COMPOUND_LEN:
        return
    limit = len(joined) + 3  # room for a plural suffix
    for i in range(n):
        acc = ""
        for j in range(i, min(n, i + k + 2)):
            acc += hay[j]
            if len(acc) > limit:
                break
            if j > i or k > 1:  # single token vs single token was checked above
                if tokens_equal(acc, joined):
                    yield i, j + 1


def contains_tokens(hay: Sequence[str], needle: Sequence[str]) -> bool:
    """True if ``needle`` occurs in ``hay`` as a contiguous run of whole tokens.

    Each token is compared with ``tokens_equal`` (plural tolerant). As a
    fallback, a run of hay tokens whose concatenation equals the concatenated
    needle also matches (``e-commerce`` ~ ``ecommerce``, ``co-founder`` ~
    ``cofounder``), still aligned on word boundaries.
    """
    return next(token_spans(hay, needle), None) is not None


def keyword_match(text: Any, keywords: Iterable[Any]) -> Optional[str]:
    """Return the first keyword (as given) mentioned in ``text``, else None.

    Matching is case- and accent-insensitive, on whole words / whole phrases,
    tolerant of simple plurals ("accountant" matches "Accountants") but never
    inside other words ("cto" does not match "director", "hr" does not match
    "three"). ``text`` may also be a list of strings (each checked on its
    own). Symbols are ignored, so "C++" behaves like "C". The one case-sensitive
    exception: a keyword that is a common short word doubling as an acronym
    ("IT", "US", "IN"; see ``_strict_tokens``) only matches that word in
    capitals, so "IT" matches "Head of IT" but not "make it grow".
    """
    if isinstance(keywords, str):
        keywords = [keywords]
    if text is None:
        return None
    if isinstance(text, (list, tuple, set, frozenset)):
        for item in text:
            hit = keyword_match(item, keywords)
            if hit is not None:
                return hit
        return None
    hay = tokenize(text)
    if not hay:
        return None
    upper: Optional[List[bool]] = None
    upper_done = False
    for kw in keywords or []:
        if kw is None:
            continue
        kw_s = str(kw)
        needle = _needle_tokens(kw_s)
        if not needle:
            continue
        strict = _strict_tokens(kw_s)
        if any(strict):
            if not upper_done:
                upper, upper_done = _upper_flags(text, hay), True
            if upper is not None:
                if _contains_strict(hay, upper, needle, strict):
                    return kw_s
                continue
        if contains_tokens(hay, needle):
            return kw_s
    return None


def _contains_strict(hay: Sequence[str], upper: Sequence[bool], needle: Sequence[str],
                     strict: Sequence[bool]) -> bool:
    """``contains_tokens`` (without the compound fallback) where strict needle
    tokens only match ALL-CAPS hay tokens."""
    k, n = len(needle), len(hay)
    for i in range(n - k + 1):
        if all(tokens_equal(hay[i + j], needle[j]) and (not strict[j] or upper[i + j]) for j in range(k)):
            return True
    return False


# --- signal stage -------------------------------------------------------------------

def _norm_type(value: Any) -> str:
    return str(value or "").strip().lower()


def _flag(value: Any, default: bool) -> bool:
    """Config boolean: None -> default; 'false' / 'no' / 'off' / '0' -> False."""
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("", "false", "no", "off", "0")
    return bool(value)


def primary_types(ctx: Any) -> Set[str]:
    """Normalised set of the playbook's primary signal types."""
    return {_norm_type(t) for t in as_str_list(ctx.playbook.signals.get("primary"))}


def is_primary(signal: Signal, ctx: Any) -> bool:
    return _norm_type(signal.type) in primary_types(ctx)


def _max_age(cfg: Dict[str, Any]) -> Optional[int]:
    raw = cfg.get("max_age_days")
    if raw is None or raw == "" or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value: Optional[int] = int(raw)
    else:
        value = to_int(raw)
    if value is None or value < 0:
        return None
    return value


def _preview(items: Sequence[str], n: int = 5) -> str:
    shown = ", ".join(items[:n])
    return shown + (f", +{len(items) - n} more" if len(items) > n else "")


class _Drops:
    """Why signals were dropped for one company (feeds the rejection reason)."""

    def __init__(self) -> None:
        self.total = 0
        self.wrong_type: Counter = Counter()
        self.excluded: Counter = Counter()
        self.no_match = 0
        self.too_old = 0

    def reason(self, match_keywords: List[str], max_age: Optional[int], types: List[str]) -> str:
        if self.total == 0:
            return "no buying signal found"
        type_only = sum(self.wrong_type.values()) == self.total
        if type_only and types:
            base = f"no signal of an accepted type [{_preview(types)}]"
        elif match_keywords:
            base = f"no signal matching [{_preview(match_keywords)}]"
        else:
            base = "no qualifying signal"
        if max_age is not None and not type_only:
            base += f" in last {max_age} days"
        parts: List[str] = []
        if self.wrong_type:
            n = sum(self.wrong_type.values())
            parts.append(f"{n} of another type ({_preview(sorted(self.wrong_type))})")
        if self.excluded:
            n = sum(self.excluded.values())
            parts.append(f"{n} excluded by keyword ({_preview(sorted(self.excluded))})")
        if self.no_match:
            parts.append(f"{self.no_match} not matching")
        if self.too_old:
            parts.append(f"{self.too_old} older than {max_age} days")
        if not parts:
            return base
        found = f"{self.total} signal{'s' if self.total != 1 else ''} found"
        return f"{base} ({found}: {'; '.join(parts)})"


def _sort_key(signal: Signal, primary: Set[str], today: Any) -> Tuple[int, int, int]:
    age = signal.age_days(today)
    return (0 if _norm_type(signal.type) in primary else 1,
            1 if age is None else 0,
            age if age is not None else 0)


def process_signals(companies: List[Company], ctx: Any) -> Tuple[List[Company], List[Tuple[Company, str]]]:
    """Apply the playbook's signal rules to every company (mutating ``company.signals``).

    Per company, in order: drop exact duplicate signals; drop signals whose
    type is not in ``signals.types`` (when set); drop signals whose title
    matches ``signals.exclude_keywords``; for primary types, require a
    ``signals.match_keywords`` hit in the title (description as fallback
    unless ``signals.match_description`` is off); record the survivors in the
    store (sets ``first_seen`` / ``reposted``); drop signals older than
    ``max_age_days``; sort primary first, then freshest (undated last).

    Several distinct postings with the same type + title that are live in the
    same batch (one role in several locations, or one ad seen by two sources)
    are concurrent openings, not evidence of a re-post: the store would flag
    the later ones against the earlier ones (even on the very first sighting),
    so ``reposted`` is only kept for them when the source itself said so.

    Returns ``(kept, rejected)`` where ``rejected`` holds ``(company, reason)``
    for companies left with no signal while ``signals.require`` is on.
    """
    cfg = ctx.playbook.signals
    types_cfg = as_str_list(cfg.get("types"))
    types = {_norm_type(t) for t in types_cfg}
    primary = primary_types(ctx)
    match_kw = as_str_list(cfg.get("match_keywords"))
    match_desc = _flag(cfg.get("match_description"), True)
    exclude_kw = as_str_list(cfg.get("exclude_keywords"))
    max_age = _max_age(cfg)
    require = bool(cfg.get("require", True))
    store = getattr(ctx, "store", None)

    kept: List[Company] = []
    rejected: List[Tuple[Company, str]] = []
    for company in companies:
        drops = _Drops()
        survivors: List[Signal] = []
        seen: Set[Tuple[Any, ...]] = set()
        for sig in company.signals or []:
            if not isinstance(sig, Signal):
                ctx.log.debug("signals: ignoring non-Signal entry on %s: %r", company.name, sig)
                continue
            ident = (sig.fingerprint, sig.external_id, sig.url, sig.location, sig.posted_at)
            if ident in seen:
                continue
            seen.add(ident)
            drops.total += 1
            stype = _norm_type(sig.type)
            if types and stype not in types:
                drops.wrong_type[stype or "untyped"] += 1
                continue
            if exclude_kw:
                hit = keyword_match(sig.title, exclude_kw)
                if hit is not None:
                    drops.excluded[hit] += 1
                    continue
            if match_kw and stype in primary:
                if keyword_match(sig.title, match_kw) is None and (
                        not match_desc or keyword_match(sig.description, match_kw) is None):
                    drops.no_match += 1
                    continue
            survivors.append(sig)

        if survivors and store is not None:
            flagged_by_source = [s.reposted for s in survivors]
            store.observe_signals(company.key, survivors, ctx.today)
            per_title = Counter(s.fingerprint for s in survivors)
            for sig, by_source in zip(survivors, flagged_by_source):
                if sig.reposted and not by_source and per_title[sig.fingerprint] > 1:
                    sig.reposted = False  # a sibling opening in this batch, not a re-post

        live: List[Signal] = []
        for sig in survivors:
            age = sig.age_days(ctx.today)
            if max_age is not None and age is not None and age > max_age:
                drops.too_old += 1
                continue
            live.append(sig)
        live.sort(key=lambda s: _sort_key(s, primary, ctx.today))
        company.signals = live

        if require and not live:
            reason = drops.reason(match_kw, max_age, types_cfg)
            ctx.log.debug("signals: rejecting %s: %s", company.name, reason)
            rejected.append((company, reason))
        else:
            kept.append(company)
    ctx.log.info("signals: %d companies with a live signal, %d rejected", len(kept), len(rejected))
    return kept, rejected


def signal_stats(company: Company, ctx: Any) -> Dict[str, Any]:
    """Summarise a company's (already processed) signals for scoring.

    Keys: ``primary`` / ``secondary`` (lists, in company order),
    ``freshest_age`` (days, primary only, None if no primary signal is dated),
    ``volume`` (number of primary signals), ``persistent`` (a primary signal
    was re-posted or is at least ``stale_after_days`` old), ``urgent`` (a
    primary title/description uses ``urgency_keywords``), ``secondary_types``
    (set of secondary signal types).
    """
    cfg = ctx.playbook.signals
    primary_set = primary_types(ctx)
    urgency = as_str_list(cfg.get("urgency_keywords"))
    stale_raw = cfg.get("stale_after_days")
    stale_after = stale_raw if isinstance(stale_raw, int) and not isinstance(stale_raw, bool) else to_int(stale_raw)

    primary: List[Signal] = []
    secondary: List[Signal] = []
    for sig in company.signals or []:
        if not isinstance(sig, Signal):
            continue
        (primary if _norm_type(sig.type) in primary_set else secondary).append(sig)

    ages: List[int] = []
    persistent = False
    for sig in primary:
        age = sig.age_days(ctx.today)
        if age is not None:
            ages.append(age)
        if sig.reposted or (age is not None and stale_after is not None and age >= stale_after):
            persistent = True
    urgent = bool(urgency) and any(
        keyword_match(s.title, urgency) is not None or keyword_match(s.description, urgency) is not None
        for s in primary)
    return {
        "primary": primary,
        "secondary": secondary,
        "freshest_age": min(ages) if ages else None,
        "volume": len(primary),
        "persistent": persistent,
        "urgent": urgent,
        "secondary_types": {_norm_type(s.type) for s in secondary if _norm_type(s.type)},
    }


__all__ = [
    "as_str_list", "contains_tokens", "is_primary", "keyword_match", "primary_types",
    "process_signals", "signal_stats", "token_spans", "tokenize", "tokens_equal",
]
