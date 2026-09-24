"""Email-pattern guessing (``type: pattern``). Offline: never touches the network.

``find(company)`` returns nothing - this finder does not discover people.
``complete(company, contact)`` fills ``contact.email_candidates`` for a known
person without an email, so the verifier can test the guesses in order (the
pipeline verifies the first few and keeps the best).

Candidate order:

1. the company's known pattern, when a previous finder (e.g. Hunter's
   domain search) left one in ``company.data['email_pattern']``;
2. candidates the contact already carried;
3. the common patterns, in this order: ``first.last``, ``first``, ``flast``,
   ``firstlast``, ``f.last``, ``first_last``, ``firstl``, ``last``.

Patterns use Hunter's notation - ``{first}``, ``{last}``, ``{f}`` (first
initial), ``{l}`` (last initial), e.g. ``{first}.{last}`` or ``{f}{last}`` (also
accepted: ``{first_name}``, ``{last_name}``, ``{first_initial}``,
``{last_initial}``) - or the shorthand above (``first.last``, ``flast``,
``lastf``, ``first-last``, ...). Anything after an ``@`` is ignored.

Names are turned into ASCII local-part tokens: accents stripped, letters such
as ``ß``/``ø``/``æ``/``ł`` transliterated, honorifics (``Dr``, ``Mr`` ...) and
trailing credentials (``Jr``, ``PhD``, ``MBA``, anything after a comma) dropped,
parenthesised text removed, then every non-alphanumeric character removed, so
hyphenated and multi-word surnames are joined (``Smith-Jones`` ->
``smithjones``, ``van der Berg`` -> ``vanderberg``).

A contact with only a first name gets only patterns that need nothing but the
first name (``{first}``); a contact without a first name, or a company without
a domain, gets no candidates. The domain is ``company.domain``, else the
``company.data['email_domain']`` hint (left by the Hunter finder when it
resolved a domain from the company name). Contacts that already have an email are
returned unchanged.

Config keys
-----------
patterns             Replace the common-pattern list (either notation).
use_company_pattern  Put ``company.data['email_pattern']`` first (default true).
max_candidates       Cap on the number of candidates (default 0 = no cap).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..models import Company, Contact
from ..utils import is_valid_email, normalize_domain, strip_accents
from .base import ContactFinder

DEFAULT_PATTERNS: Tuple[str, ...] = (
    "{first}.{last}", "{first}", "{f}{last}", "{first}{last}", "{f}.{last}", "{first}_{last}",
    "{first}{l}", "{last}",
)

_PLACEHOLDER_ALIASES: Dict[str, str] = {
    "first": "first", "first_name": "first", "firstname": "first", "fn": "first",
    "last": "last", "last_name": "last", "lastname": "last", "ln": "last",
    "f": "f", "fi": "f", "first_initial": "f",
    "l": "l", "li": "l", "last_initial": "l",
}
_PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")
_SEPARATORS = ".-_"
_TRANSLIT = str.maketrans({
    "ß": "ss", "æ": "ae", "Æ": "ae", "ø": "o", "Ø": "o", "œ": "oe", "Œ": "oe", "ł": "l", "Ł": "l",
    "đ": "d", "Đ": "d", "ð": "d", "Ð": "d", "þ": "th", "Þ": "th", "ı": "i", "ĸ": "k", "ŋ": "n",
})
_SHORTHAND_WORDS: Tuple[Tuple[str, str], ...] = (
    ("firstname", "{first}"), ("lastname", "{last}"), ("first", "{first}"), ("last", "{last}"),
    ("f", "{f}"), ("l", "{l}"),
)
_HONORIFICS = frozenset({"dr", "mr", "mrs", "ms", "miss", "mx", "prof", "sir"})
_CREDENTIALS = frozenset({"jr", "sr", "ii", "iii", "iv", "phd", "md", "mba", "cpa", "cfa", "esq", "pmp"})


def name_token(raw: Any, *, is_first: bool = False) -> str:
    """ASCII, lowercase, alphanumeric-only form of a (first or last) name part."""
    s = str(raw or "")
    s = re.sub(r"\([^)]*\)", " ", s)          # "Jane (She/Her)"
    s = s.split(",", 1)[0]                     # "Doe, CPA"
    s = strip_accents(s.translate(_TRANSLIT)).lower()
    words = [re.sub(r"[^a-z0-9]", "", w) for w in s.split()]
    words = [w for w in words if w]
    if is_first:
        while len(words) > 1 and words[0] in _HONORIFICS:
            words.pop(0)
    while len(words) > 1 and words[-1] in _CREDENTIALS:
        words.pop()
    return "".join(words)


def _shorthand_to_template(pattern: str) -> Optional[str]:
    """'first.last' -> '{first}.{last}', 'flast' -> '{f}{last}'; None if not parseable."""
    out: List[str] = []
    i, s = 0, pattern.strip().lower()
    if not s:
        return None
    while i < len(s):
        for word, token in _SHORTHAND_WORDS:
            if s.startswith(word, i):
                out.append(token)
                i += len(word)
                break
        else:
            if s[i] in _SEPARATORS:
                out.append(s[i])
                i += 1
            else:
                return None
    return "".join(out)


def normalize_pattern(pattern: Any) -> Optional[str]:
    """Canonical ``{first}.{last}``-style template for a Hunter-style or shorthand pattern."""
    if not isinstance(pattern, str) or not pattern.strip():
        return None
    p = pattern.strip().split("@", 1)[0].strip()
    if not p:
        return None
    if "{" not in p:
        return _shorthand_to_template(p)
    bad = False

    def repl(m: "re.Match[str]") -> str:
        nonlocal bad
        key = _PLACEHOLDER_ALIASES.get(m.group(1).strip().lower())
        if key is None:
            bad = True
            return ""
        return "{" + key + "}"

    out = _PLACEHOLDER_RE.sub(repl, p).lower()
    if bad or "{" not in out:
        return None
    literal = _PLACEHOLDER_RE.sub("", out)
    if any(not (ch.isalnum() or ch in _SEPARATORS) for ch in literal):
        return None
    return out


def render_pattern(template: str, first: str, last: str) -> Optional[str]:
    """Fill a canonical template; None when it needs a name part we do not have."""
    values = {"first": first, "last": last, "f": first[:1], "l": last[:1]}
    missing = False

    def repl(m: "re.Match[str]") -> str:
        nonlocal missing
        v = values.get(m.group(1), "")
        if not v:
            missing = True
        return v

    local = _PLACEHOLDER_RE.sub(repl, template)
    if missing or not local:
        return None
    local = local.strip(_SEPARATORS)
    return local or None


def contact_name_parts(contact: Contact) -> Tuple[str, str]:
    """(first, last) name tokens for pattern building.

    Recovers a missing last name from ``full_name`` (or from a first-name field
    holding the whole name), skips a first name that is only an honorific
    ("Dr" + "Jane Doe"), and ignores provider-obfuscated last names ("Sm***h").
    """
    first_raw = re.sub(r"\([^)]*\)", " ", contact.first_name or "").strip()
    last_raw = (contact.last_name or "").strip()
    full = [w for w in re.sub(r"\([^)]*\)", " ", contact.full_name or "").split() if w]
    if not first_raw and full:
        last_words = last_raw.split()
        if last_words and [name_token(w) for w in full[-len(last_words):]] == [name_token(w) for w in last_words]:
            # full_name is "<first...> <last>" or just the last name (Contact builds it that way)
            rest = full[:len(full) - len(last_words)]
            first_raw = rest[0] if rest else ""
        else:
            first_raw = full[0]
            if not last_raw:
                last_raw = " ".join(full[1:])
    first_words = first_raw.split()
    while len(first_words) > 1 and name_token(first_words[0]) in _HONORIFICS:
        first_words.pop(0)
    first_raw = " ".join(first_words)
    if not last_raw:
        if len(first_words) > 1:  # whole name in the first-name field
            first_raw, last_raw = first_words[0], " ".join(first_words[1:])
        elif len(full) > 1 and name_token(full[0], is_first=True) == name_token(first_raw, is_first=True):
            last_raw = " ".join(full[1:])
    if name_token(first_raw) in _HONORIFICS:  # "Dr" + "Jane Doe" -> Jane Doe; "Dr" + "Doe" -> no first name
        rest = last_raw.split()
        first_raw = rest[0] if len(rest) > 1 else ""
        last_raw = " ".join(rest[1:]) if len(rest) > 1 else last_raw
    if "*" in last_raw:
        last_raw = ""
    return name_token(first_raw, is_first=True), name_token(last_raw)


class PatternFinder(ContactFinder):
    """Guess email addresses from name + domain (see module docstring)."""

    name = "pattern"
    env_key = ""
    offline = True

    def find(self, company: Company) -> List[Contact]:
        return []

    def _patterns(self) -> List[str]:
        raw = self.config.get("patterns")
        source: Sequence[Any] = DEFAULT_PATTERNS if not raw else ([raw] if isinstance(raw, str) else raw)
        out: List[str] = []
        for p in source:
            t = normalize_pattern(p)
            if t is None:
                self.log.warning("pattern finder: ignoring unrecognised pattern %r", p)
            elif t not in out:
                out.append(t)
        return out

    def candidates(self, company: Company, contact: Contact) -> List[str]:
        """Ordered, deduplicated guesses for ``contact`` at ``company`` (may be empty)."""
        domain = company.domain or normalize_domain((company.data or {}).get("email_domain"))
        first, last = contact_name_parts(contact)
        if not domain or not first:
            return []
        known: List[str] = []
        raw_known = (company.data or {}).get("email_pattern")
        if raw_known and self.config.get("use_company_pattern", True):
            template = normalize_pattern(raw_known)
            if template:
                known.append(template)
            else:
                self.log.debug("pattern finder: unusable company pattern %r for %s", raw_known, domain)
        out: List[str] = []

        def add(email: str) -> None:
            e = str(email or "").strip().lower()
            if e and e not in out and is_valid_email(e):
                out.append(e)

        def add_template(template: str) -> None:
            local = render_pattern(template, first, last)
            if local:
                add(f"{local}@{domain}")

        for template in known:
            add_template(template)
        for existing in contact.email_candidates:
            add(existing)
        for template in self._patterns():
            add_template(template)
        try:
            cap = int(self.config.get("max_candidates") or 0)
        except (TypeError, ValueError):
            raise ValueError(f"pattern finder: max_candidates must be an integer, "
                             f"got {self.config.get('max_candidates')!r}") from None
        return out[:cap] if cap > 0 else out

    def complete(self, company: Company, contact: Contact) -> Contact:
        if contact.email:
            return contact
        found = self.candidates(company, contact)
        if found:
            contact.email_candidates = found
            contact.data.setdefault("email_candidates_source", self.name)
        return contact


__all__ = ["DEFAULT_PATTERNS", "PatternFinder", "contact_name_parts", "name_token", "normalize_pattern",
           "render_pattern"]
