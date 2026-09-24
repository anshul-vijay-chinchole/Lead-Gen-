"""Stage 3 - ICP FILTER: does this company look like the ideal customer?

``apply_icp`` splits companies into kept / rejected (with a readable reason),
``check_icp`` explains a single company, and ``fit_checks`` reports how well
location, size and industry fit (used by the scorer).

Playbook keys read (section ``icp``):

``exclude_domains``
    Domains never to contact (competitors, clients). A domain also excludes
    its subdomains (``acme.com`` excludes ``eu.acme.com``).
``exclude_company_patterns``
    Regular expressions searched (case-insensitively) in the company name.
``require_domain``
    Reject companies without a website/domain.
``exclude_keywords``
    Reject when one is mentioned in the name, industry, keywords or
    description.
``keywords``
    When set, at least one must be mentioned in the name, industry, keywords,
    description or a signal title.
``locations`` / ``exclude_locations``
    Places, matched as whole words against ``company.location``,
    ``company.country`` and the locations of the company's primary signals
    (where it is hiring / acting). A small generic alias table makes common
    spellings equivalent: ``United States`` ~ ``US`` ~ ``USA`` ~ ``U.S.``;
    ``United Kingdom`` ~ ``UK`` ~ ``GB`` ~ ``Great Britain`` (and
    ``England`` / ``Scotland`` / ``Wales`` / ``Northern Ireland`` count as
    UK, not the other way round); US state names <-> 2-letter codes (a code
    is recognised in upper case after a comma - ``Austin, TX`` - or as the
    whole value) and states count as United States; a ``country`` field that
    is an ISO code (``DE``, ``GBR``) is read as that country; a few regions
    (``Europe``, ``North America``, ``DACH``, ``Nordics``, ``Benelux``,
    ``ANZ``) cover their member countries. Anything else is a plain phrase
    match (``London``, ``Bay Area``). Nothing ever matches inside a word:
    ``US`` does not match ``Houston``.
    A company is excluded when its own location/country matches
    ``exclude_locations``; signal locations are only used for exclusion when
    the company's own location is unknown, and then only if *all* of them
    are excluded (one posting abroad does not disqualify a company).
``employees``
    ``{min, max}`` head-count range (either may be null).
``industries`` / ``exclude_industries``
    Positive matches look in industry, keywords and description; exclusions
    only in industry and keywords (a description often mentions other
    industries - "we serve retailers").
``unknown_passes``
    A company whose location / size / industry is unknown (empty location
    fields, ``employees`` None, industry + keywords + description all empty)
    passes that check only when this is true. Location strings that are not
    places ("Remote", "Anywhere", "Multiple locations") count as unknown.

Checks run in this order and the first failure is the reason: suppression
list, excluded domain, excluded name pattern, required domain, excluded
keyword, required keyword, location, size, industry.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Dict, FrozenSet, List, NamedTuple, Optional, Sequence, Set, Tuple

from .models import Company
from .signals import as_str_list, keyword_match, primary_types
from .utils import normalize_domain, normalize_text, to_int

# --- location knowledge (generic, small) -----------------------------------------

# canonical country -> (name aliases, codes). Aliases are whole-word phrases.
_COUNTRIES: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "united states": (("united states", "united states of america", "usa", "us", "u s", "u s a"),
                      ("US", "USA")),
    "united kingdom": (("united kingdom", "uk", "u k", "great britain", "britain", "gb"),
                       ("GB", "UK", "GBR")),
    "canada": (("canada",), ("CA", "CAN")),
    "australia": (("australia",), ("AU", "AUS")),
    "new zealand": (("new zealand",), ("NZ", "NZL")),
    "ireland": (("ireland", "republic of ireland", "eire"), ("IE", "IRL")),
    "germany": (("germany", "deutschland"), ("DE", "DEU")),
    "france": (("france",), ("FR", "FRA")),
    "netherlands": (("netherlands", "the netherlands", "holland"), ("NL", "NLD")),
    "belgium": (("belgium",), ("BE", "BEL")),
    "luxembourg": (("luxembourg",), ("LU", "LUX")),
    "spain": (("spain", "espana"), ("ES", "ESP")),
    "portugal": (("portugal",), ("PT", "PRT")),
    "italy": (("italy", "italia"), ("IT", "ITA")),
    "switzerland": (("switzerland", "schweiz", "suisse"), ("CH", "CHE")),
    "austria": (("austria", "osterreich"), ("AT", "AUT")),
    "sweden": (("sweden",), ("SE", "SWE")),
    "norway": (("norway",), ("NO", "NOR")),
    "denmark": (("denmark",), ("DK", "DNK")),
    "finland": (("finland",), ("FI", "FIN")),
    "iceland": (("iceland",), ("IS", "ISL")),
    "poland": (("poland",), ("PL", "POL")),
    "india": (("india",), ("IN", "IND")),
    "singapore": (("singapore",), ("SG", "SGP")),
    "united arab emirates": (("united arab emirates", "uae"), ("AE", "ARE")),
    "israel": (("israel",), ("IL", "ISR")),
    "japan": (("japan",), ("JP", "JPN")),
    "south africa": (("south africa",), ("ZA", "ZAF")),
    "brazil": (("brazil", "brasil"), ("BR", "BRA")),
    "mexico": (("mexico",), ("MX", "MEX")),
    "hong kong": (("hong kong",), ("HK", "HKG")),
    "philippines": (("philippines",), ("PH", "PHL")),
}

_US_STATES: Dict[str, str] = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california",
    "CO": "colorado", "CT": "connecticut", "DE": "delaware", "FL": "florida", "GA": "georgia",
    "HI": "hawaii", "ID": "idaho", "IL": "illinois", "IN": "indiana", "IA": "iowa",
    "KS": "kansas", "KY": "kentucky", "LA": "louisiana", "ME": "maine", "MD": "maryland",
    "MA": "massachusetts", "MI": "michigan", "MN": "minnesota", "MS": "mississippi",
    "MO": "missouri", "MT": "montana", "NE": "nebraska", "NV": "nevada", "NH": "new hampshire",
    "NJ": "new jersey", "NM": "new mexico", "NY": "new york", "NC": "north carolina",
    "ND": "north dakota", "OH": "ohio", "OK": "oklahoma", "OR": "oregon", "PA": "pennsylvania",
    "RI": "rhode island", "SC": "south carolina", "SD": "south dakota", "TN": "tennessee",
    "TX": "texas", "UT": "utah", "VT": "vermont", "VA": "virginia", "WA": "washington",
    "WV": "west virginia", "WI": "wisconsin", "WY": "wyoming", "DC": "district of columbia",
}

# sub-regions -> the country they belong to (never the other way round)
_PARTS: Dict[str, str] = {
    "england": "united kingdom", "scotland": "united kingdom", "wales": "united kingdom",
    "northern ireland": "united kingdom",
    "new south wales": "australia",           # so "wales" inside it is not read as UK
    "new england": "united states",           # so "england" inside it is not read as UK
    "washington dc": "united states",
}
_PARTS.update({name: "united states" for name in _US_STATES.values()})

# regions -> (aliases, member countries)
_REGIONS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "europe": (("europe", "eu", "european union"),
               ("united kingdom", "ireland", "germany", "france", "netherlands", "belgium",
                "luxembourg", "spain", "portugal", "italy", "switzerland", "austria", "sweden",
                "norway", "denmark", "finland", "iceland", "poland")),
    "north america": (("north america",), ("united states", "canada", "mexico")),
    "dach": (("dach",), ("germany", "austria", "switzerland")),
    "nordics": (("nordics", "nordic", "scandinavia"), ("sweden", "norway", "denmark", "finland", "iceland")),
    "benelux": (("benelux",), ("belgium", "netherlands", "luxembourg")),
    "anz": (("anz",), ("australia", "new zealand")),
}

# Words that say nothing about *where* ("Remote", "Multiple locations", "N/A").
_PLACELESS = frozenset({
    "remote", "anywhere", "worldwide", "global", "globally", "hybrid", "onsite", "on", "site",
    "office", "in", "or", "and", "from", "home", "wfh", "work", "based", "flexible",
    "multiple", "various", "several", "locations", "location", "n", "a", "tbd", "tbc",
    "unknown", "none", "not", "specified", "other",
})


def _build_tables() -> Tuple[Dict[Tuple[str, ...], str], Dict[str, str], Dict[str, Set[str]]]:
    phrases: Dict[Tuple[str, ...], str] = {}
    codes: Dict[str, str] = {}
    parents: Dict[str, Set[str]] = {}
    for canon, (aliases, cc) in _COUNTRIES.items():
        for a in aliases + (canon,):
            phrases[tuple(normalize_text(a).split())] = canon
        for c in cc:
            codes[c] = canon
    for part, country in _PARTS.items():
        phrases[tuple(normalize_text(part).split())] = part
        parents.setdefault(part, set()).add(country)
    for region, (aliases, members) in _REGIONS.items():
        for a in aliases + (region,):
            phrases[tuple(normalize_text(a).split())] = region
        for m in members:
            parents.setdefault(m, set()).add(region)
    return phrases, codes, parents


_PHRASES, _CODES, _PARENTS = _build_tables()
_MAX_PHRASE = max(len(p) for p in _PHRASES)
_STATE_AFTER_COMMA = re.compile(r",\s*([A-Z]{2})(?![A-Za-z])")


def _with_parents(ids: Set[str]) -> FrozenSet[str]:
    out = set(ids)
    todo = list(ids)
    while todo:
        for parent in _PARENTS.get(todo.pop(), ()):
            if parent not in out:
                out.add(parent)
                todo.append(parent)
    return frozenset(out)


@lru_cache(maxsize=4096)
def place_ids(text: str, country_field: bool = False) -> FrozenSet[str]:
    """Canonical places mentioned in a location string (plus what they imply).

    'Austin, TX' -> {texas, united states, north america};
    'Manchester, England' -> {england, united kingdom, europe}.
    """
    s = str(text or "").strip()
    if not s:
        return frozenset()
    ids: Set[str] = set()
    compact = re.sub(r"[\s.]", "", s).upper()
    if country_field and compact in _CODES:
        ids.add(_CODES[compact])
    elif re.fullmatch(r"[A-Z]{2}", s) and s in _US_STATES:
        ids.add(_US_STATES[s])
    for m in _STATE_AFTER_COMMA.finditer(s):
        code = m.group(1)
        if code in _US_STATES:
            ids.add(_US_STATES[code])
        elif code in _CODES:
            ids.add(_CODES[code])
    tokens = normalize_text(s).split()
    i = 0
    while i < len(tokens):
        for size in range(min(_MAX_PHRASE, len(tokens) - i), 0, -1):
            hit = _PHRASES.get(tuple(tokens[i:i + size]))
            if hit is not None:
                ids.add(hit)
                i += size
                break
        else:
            i += 1
    return _with_parents(ids)


@lru_cache(maxsize=1024)
def canonical_place(criterion: str) -> Optional[str]:
    """Canonical id for a configured location ('USA' -> 'united states', 'TX' -> 'texas'),
    or None when it is not in the alias table (then it is matched as a phrase)."""
    hit = _PHRASES.get(tuple(normalize_text(criterion).split()))
    if hit is not None:
        return hit
    compact = re.sub(r"[\s.]", "", str(criterion or "")).upper()
    if len(compact) == 2 and compact in _US_STATES:
        return _US_STATES[compact]
    return _CODES.get(compact)


def is_placeless(text: Any, country_field: bool = False) -> bool:
    """True for empty strings and ones like 'Remote', 'Multiple locations', 'N/A'
    (a value that names a known place, such as the code 'IN', never is)."""
    tokens = normalize_text(text).split()
    if not all(t in _PLACELESS for t in tokens):
        return False
    return not (tokens and place_ids(str(text), country_field))


def match_location(criteria: Sequence[str], text: str, country_field: bool = False) -> Optional[str]:
    """First configured location (as written) that ``text`` is in, else None."""
    if not text:
        return None
    ids = place_ids(str(text), country_field)
    for crit in criteria:
        canon = canonical_place(crit)
        if canon is not None:
            if canon in ids:
                return crit
        elif keyword_match(text, [crit]) is not None:
            return crit
    return None


# --- fit checks -------------------------------------------------------------------------

class FitResult(NamedTuple):
    """Outcome of one fit dimension.

    ``status``: True (fits, or the criterion is not configured) / False
    (mismatch) / None (data unknown). ``configured``: whether the playbook
    sets a criterion for it. ``reason``: human-readable explanation.
    """

    status: Optional[bool]
    configured: bool
    reason: str


def _company_keywords(company: Company) -> List[str]:
    return as_str_list(company.keywords)


def _location_fit(company: Company, ctx: Any) -> FitResult:
    icp = ctx.playbook.icp
    include = as_str_list(icp.get("locations"))
    exclude = as_str_list(icp.get("exclude_locations"))
    if not include and not exclude:
        return FitResult(True, False, "")
    home: List[Tuple[str, bool]] = [(str(t), cf) for t, cf in ((company.location, False), (company.country, True))
                                    if t and not is_placeless(t, cf)]
    ptypes = primary_types(ctx)
    sig_places: List[Tuple[str, bool]] = []
    for sig in company.signals or []:
        loc = getattr(sig, "location", "")
        if loc and str(getattr(sig, "type", "")).strip().lower() in ptypes and not is_placeless(loc):
            if (str(loc), False) not in sig_places:
                sig_places.append((str(loc), False))
    if not home and not sig_places:
        return FitResult(None, True, "location unknown")

    if exclude:
        for text, cf in home:
            hit = match_location(exclude, text, cf)
            if hit is not None:
                return FitResult(False, True, f"excluded location {hit!r} ({text})")
        if not home and sig_places:
            hits = [match_location(exclude, t, cf) for t, cf in sig_places]
            if all(h is not None for h in hits):
                where = ", ".join(t for t, _ in sig_places[:3])
                return FitResult(False, True, f"excluded location {hits[0]!r} (signals in {where})")

    if include:
        for text, cf in home + sig_places:
            hit = match_location(include, text, cf)
            if hit is not None:
                return FitResult(True, True, f"location match ({hit})")
        got = "; ".join(t for t, _ in (home + sig_places)[:3])
        return FitResult(False, True, f"location not in ICP [{_preview(include)}] (got: {got})")
    return FitResult(True, True, "location not excluded")


def _employee_bounds(ctx: Any) -> Tuple[Optional[int], Optional[int]]:
    emp = ctx.playbook.icp.get("employees") or {}
    if not isinstance(emp, dict):
        return None, None
    return to_int(emp.get("min")), to_int(emp.get("max"))


def _size_fit(company: Company, ctx: Any) -> FitResult:
    lo, hi = _employee_bounds(ctx)
    if lo is None and hi is None:
        return FitResult(True, False, "")
    n = company.employees
    if n is None:
        return FitResult(None, True, "size unknown")
    if lo is not None and n < lo:
        return FitResult(False, True, f"too small ({n} employees, min {lo})")
    if hi is not None and n > hi:
        return FitResult(False, True, f"too large ({n} employees, max {hi})")
    return FitResult(True, True, f"size match ({n} employees)")


def _industry_fit(company: Company, ctx: Any) -> FitResult:
    icp = ctx.playbook.icp
    include = as_str_list(icp.get("industries"))
    exclude = as_str_list(icp.get("exclude_industries"))
    if not include and not exclude:
        return FitResult(True, False, "")
    kws = _company_keywords(company)
    if not (company.industry or kws or company.description):
        return FitResult(None, True, "industry unknown")
    if exclude:
        hit = keyword_match(company.industry, exclude) or keyword_match(kws, exclude)
        if hit is not None:
            got = company.industry or ", ".join(kws[:3])
            return FitResult(False, True, f"excluded industry {hit!r} ({got})")
    if include:
        hit = (keyword_match(company.industry, include) or keyword_match(kws, include)
               or keyword_match(company.description, include))
        if hit is not None:
            return FitResult(True, True, f"industry match ({hit})")
        got = company.industry or ", ".join(kws[:3]) or "description only"
        return FitResult(False, True, f"industry not in ICP [{_preview(include)}] (got: {got})")
    return FitResult(True, True, "industry not excluded")


def fit_report(company: Company, ctx: Any) -> Dict[str, FitResult]:
    """Detailed fit per dimension: ``{'location', 'size', 'industry'} -> FitResult``."""
    return {
        "location": _location_fit(company, ctx),
        "size": _size_fit(company, ctx),
        "industry": _industry_fit(company, ctx),
    }


def fit_checks(company: Company, ctx: Any) -> Dict[str, Optional[bool]]:
    """``{'location', 'size', 'industry'}`` -> True (match / not configured),
    False (mismatch) or None (data unknown)."""
    return {k: v.status for k, v in fit_report(company, ctx).items()}


# --- the filter ---------------------------------------------------------------------------

def _preview(items: Sequence[str], n: int = 5) -> str:
    shown = ", ".join(items[:n])
    return shown + (f", +{len(items) - n} more" if len(items) > n else "")


def _domain_excluded(domain: str, excluded: Sequence[str]) -> Optional[str]:
    for raw in excluded:
        d = normalize_domain(raw)
        if d and (domain == d or domain.endswith("." + d)):
            return d
    return None


def check_icp(company: Company, ctx: Any) -> Optional[str]:
    """Return why ``company`` is outside the ICP, or None when it fits."""
    icp = ctx.playbook.icp
    domain = company.domain or ""
    store = getattr(ctx, "store", None)

    if domain and store is not None and store.is_suppressed(domain=domain):
        return f"domain {domain} is on the suppression list"

    if domain:
        hit_domain = _domain_excluded(domain, as_str_list(icp.get("exclude_domains")))
        if hit_domain is not None:
            suffix = "" if hit_domain == domain else f" (under {hit_domain})"
            return f"excluded domain {domain}{suffix}"

    for pattern in as_str_list(icp.get("exclude_company_patterns")):
        try:
            if re.search(pattern, company.name or "", re.IGNORECASE):
                return f"company name {company.name!r} matches excluded pattern {pattern!r}"
        except re.error as e:
            ctx.log.warning("icp: ignoring invalid exclude_company_patterns entry %r: %s", pattern, e)

    if icp.get("require_domain") and not domain:
        return "no website/domain (icp.require_domain is on)"

    kws = _company_keywords(company)
    exclude_kw = as_str_list(icp.get("exclude_keywords"))
    if exclude_kw:
        for label, value in (("name", company.name), ("industry", company.industry),
                             ("keywords", kws), ("description", company.description)):
            hit = keyword_match(value, exclude_kw)
            if hit is not None:
                return f"excluded keyword {hit!r} in {label}"

    want_kw = as_str_list(icp.get("keywords"))
    if want_kw:
        titles = [s.title for s in company.signals or [] if getattr(s, "title", "")]
        fields: List[Any] = [company.name, company.industry, kws, company.description, titles]
        if not any(keyword_match(v, want_kw) is not None for v in fields):
            return (f"no ICP keyword [{_preview(want_kw)}] in name, industry, keywords, "
                    f"description or signals")

    unknown_passes = bool(icp.get("unknown_passes", True))
    for result in fit_report(company, ctx).values():
        if result.status is False:
            return result.reason
        if result.status is None and not unknown_passes:
            return f"{result.reason} (icp.unknown_passes is off)"
    return None


def apply_icp(companies: List[Company], ctx: Any) -> Tuple[List[Company], List[Tuple[Company, str]]]:
    """Split companies into ``(kept, [(company, reason), ...])`` by ``check_icp``."""
    kept: List[Company] = []
    rejected: List[Tuple[Company, str]] = []
    for company in companies:
        reason = check_icp(company, ctx)
        if reason is None:
            kept.append(company)
        else:
            ctx.log.debug("icp: rejecting %s: %s", company.name, reason)
            rejected.append((company, reason))
    ctx.log.info("icp: %d companies match, %d rejected", len(kept), len(rejected))
    return kept, rejected


__all__ = [
    "FitResult", "apply_icp", "canonical_place", "check_icp", "fit_checks", "fit_report",
    "is_placeless", "match_location", "place_ids",
]
