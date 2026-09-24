"""Copy guardrails: cheap, deterministic checks every written sequence must pass.

``check_message(message, lead, ctx, step_index)`` returns a list of readable
problems for one email (empty = OK); ``check_sequence(messages, lead, ctx)``
checks a whole sequence. Every problem is prefixed with ``step N:`` so it can
be fed straight back to an LLM or shown in an export's notes.

Checks (``step_index`` is 0-based; step 1 = index 0)
----------------------------------------------------
* empty body;
* body longer than ``writer.max_words`` words - counted WITHOUT the signature
  and footer the writers append (``offer.signature`` / ``offer.footer``);
* banned phrases (``writer.banned_phrases``, case/punctuation-insensitive; any
  script - Cyrillic, Greek, CJK ... phrases are matched too);
* leftover placeholders: ``{x}``, ``{{x}}``, ``[Name]``, ``[Your Company]``,
  ``<first_name>`` (and any other ``<tag>`` or ``<instruction text>`` such as an
  echoed prompt example: plain text only);
* step 1 subject empty, longer than 8 words or longer than 70 characters;
* step 1 must mention the company name (or its domain) or the top signal's title
  (matching is Unicode-aware: Latin needles match whole words, needles in other
  scripts match anywhere - inflected forms, languages written without spaces);
* more than one exclamation mark (subject + body);
* links other than ``offer.booking_link`` (same host; same path or a sub-path of
  it) or pages on ``offer.sender_website``'s domain (``http(s)://`` and ``www.``
  links; bare domains are not treated as links);
* spammy wording: ``free!``, ``guarantee``, ``risk-free``, ``act now``, ``100%``,
  ``$$$``, ``click here``;
* ALL-CAPS words longer than 4 letters, except known acronyms
  (``KNOWN_ACRONYMS``, anything spelled that way in the company / sender /
  signal / contact data, and the optional ``writer.acronyms`` list).

``check_sequence`` adds: number of messages != ``len(writer.sequence)`` and
identical bodies in two steps.

``check_snippet(text, lead, ctx)`` runs the content checks (banned phrases,
placeholders, links, spam, ALL-CAPS) on a short text such as the AI's
``personalization_line``, which exporters send as a merge variable.

The signature/footer are excluded from content checks (a footer may carry
an address or opt-out link).
"""
from __future__ import annotations

import re
from typing import Any, Iterable, List, Optional, Set, Tuple
from urllib.parse import urlparse

from ..models import Lead, Message
from ..utils import normalize_company_name, normalize_domain, strip_accents, word_count
from .template import clean_title, company_display_name, sequence_steps, signoff_for, strip_signoff

MAX_SUBJECT_WORDS = 8
MAX_SUBJECT_CHARS = 70
MAX_EXCLAMATIONS = 1

KNOWN_ACRONYMS = frozenset({
    "HIPAA", "COVID", "NASDAQ", "EBITDA", "CAPEX", "FINRA", "ICAEW", "AICPA", "UNESCO", "ASEAN",
    "NAFTA", "USMCA", "SWIFT", "SCADA", "BREEAM", "TOGAF", "COBIT", "CISSP", "NEBOSH", "LATAM",
    "HITRUST", "FEDRAMP", "FMCSA", "DEFRA", "OFSTED", "USGAAP", "CFIUS", "MIFID", "UCITS", "SOLAS",
    "IATA", "OKRS", "KPIS",
})

SPAM_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("free!", re.compile(r"\bfree\s*!", re.I)),
    ("guarantee", re.compile(r"\bguarantee", re.I)),
    ("risk-free", re.compile(r"\brisk[\s-]*free\b", re.I)),
    ("act now", re.compile(r"\bact\s+now\b", re.I)),
    ("100%", re.compile(r"\b100\s*%")),
    ("$$$", re.compile(r"\$\s*\$\s*\$")),
    ("click here", re.compile(r"\bclick\s+here\b", re.I)),
)

PLACEHOLDER_PATTERNS: Tuple["re.Pattern[str]", ...] = (
    re.compile(r"\{\{?[^{}\n]{1,60}\}?\}"),                         # {first_name}, {{company}}
    re.compile(r"\[\s*[A-Za-z][A-Za-z _'’/-]{0,40}\s*\]"),           # [Name], [Your Company]
    re.compile(r"<\s*/?\s*[A-Za-z][A-Za-z _-]{0,30}\s*/?\s*>"),      # <first_name>, <br>
    # <one sentence: the problem ...> (an echoed prompt example); not <https://...> or <a@b.com>
    re.compile(r"<\s*(?!(?:https?|mailto|ftp):|www\.)[A-Za-z][^<>@\n]{0,120}>"),
)

URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>()\[\]{}\"']+", re.I)
CAPS_RE = re.compile(r"\b[A-Z]{5,}\b")


_NON_WORD = re.compile(r"[\W_]+")


def _norm(text: Any) -> str:
    """Like ``utils.normalize_text`` but Unicode-aware: letters of every script survive.

    (``normalize_text`` keeps only ``[a-z0-9]``, so Cyrillic / Greek / CJK copy
    would normalise to nothing and never match a company name or banned phrase.)
    """
    t = strip_accents(str(text or "")).casefold()
    return " ".join(_NON_WORD.sub(" ", t).split())


def _contains_any(text: Any, needles: Iterable[str]) -> Optional[str]:
    """First needle found in ``text`` (case/accent/punctuation-insensitive), else None.

    Latin (ASCII after normalising) needles must match whole words; needles in
    other scripts match as substrings - inflection ('Яндексе') and languages
    written without spaces (Japanese, Chinese, Thai) defeat word boundaries.
    """
    hay = _norm(text)
    for n in needles:
        nn = _norm(n)
        if not nn:
            continue
        if nn.isascii():
            if re.search(r"(?<![a-z0-9])" + re.escape(nn) + r"(?![a-z0-9])", hay):
                return n
        elif nn in hay:
            return n
    return None


def _url_parts(url: str) -> Tuple[str, str]:
    """``(host without www., lowercased path without trailing /)``; ('', '') if unparseable."""
    u = url.strip()
    if not re.match(r"^[a-z][a-z0-9+.-]*://", u, re.I):
        u = "http://" + u
    try:
        parsed = urlparse(u)
        host = (parsed.hostname or "").lower().strip(".")
    except ValueError:
        return "", ""
    if host.startswith("www."):
        host = host[4:]
    return host, parsed.path.rstrip("/").lower()


def _url_allowed(url: str, ctx: Any) -> bool:
    offer = ctx.playbook.offer or {}
    booking = str(offer.get("booking_link") or "").strip()
    website = str(offer.get("sender_website") or "").strip()
    if booking:
        # same host exactly (no 'cal.com.evil.io' / 'cal.com@evil.io') and the booking path
        # itself or a sub-path of it (no 'calendly.com/sammy-scam' for 'calendly.com/sam')
        b_host, b_path = _url_parts(booking)
        u_host, u_path = _url_parts(url)
        if b_host and u_host == b_host and (u_path == b_path or u_path.startswith(b_path + "/")):
            return True
    site = normalize_domain(website)
    return bool(site) and normalize_domain(url) == site


def _caps_allowed(lead: Lead, ctx: Any) -> Set[str]:
    allowed: Set[str] = set(KNOWN_ACRONYMS)
    offer = ctx.playbook.offer or {}
    sources: List[str] = [lead.company.name or "", lead.company.industry or "", lead.company.location or ""]
    sources += [s.title or "" for s in lead.company.signals]
    if lead.contact:
        sources += [lead.contact.title or "", lead.contact.full_name or ""]
    sources += [str(offer.get(k) or "") for k in ("sender_company", "sender_name", "service", "value_prop",
                                                   "proof", "cta")]
    for src in sources:
        allowed.update(CAPS_RE.findall(src))
    extra = ctx.playbook.writer.get("acronyms") or []
    if isinstance(extra, str):
        extra = [extra]
    allowed.update(str(a).upper() for a in extra if a)
    return allowed


def _mention_needles(lead: Lead) -> List[str]:
    needles: List[str] = []
    core = normalize_company_name(lead.company.name)
    if core:
        needles.append(core)
    display = company_display_name(lead.company.name or "")
    if display:
        needles.append(display)
    if lead.company.name:
        needles.append(lead.company.name)
    root = normalize_domain(lead.company.domain).split(".")[0] if lead.company.domain else ""
    if len(root) >= 3:
        needles.append(root)
    top = lead.top_signal
    if top is not None and top.title:
        needles.append(top.title)
        short = clean_title(top.title)
        if short:
            needles.append(short)
    return _dedupe(n for n in needles if _norm(n))


def _dedupe(items: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _banned(text: str, ctx: Any) -> List[str]:
    return [f'uses banned phrase "{phrase}"' for phrase in ctx.playbook.writer.get("banned_phrases") or []
            if phrase and _contains_any(text, [str(phrase)])]


def _placeholders(text: str) -> List[str]:
    leftovers: List[str] = []
    for pat in PLACEHOLDER_PATTERNS:
        leftovers += [m.group(0) for m in pat.finditer(text)]
    return [f"leftover placeholder or markup {tok}" for tok in _dedupe(leftovers)]


def _bad_links(text: str, ctx: Any) -> List[str]:
    return [f"link not allowed: {url} (only offer.booking_link / offer.sender_website)"
            for url in _dedupe(u.rstrip(".,;:!?)'\"") for u in URL_RE.findall(text))
            if not _url_allowed(url, ctx)]


def _spam(text: str) -> List[str]:
    return [f'spammy wording "{label}"' for label, pat in SPAM_PATTERNS if pat.search(text)]


def _caps(text: str, lead: Lead, ctx: Any) -> List[str]:
    allowed = _caps_allowed(lead, ctx)
    return [f'ALL-CAPS word "{word}"' for word in _dedupe(CAPS_RE.findall(text)) if word not in allowed]


def check_snippet(text: str, lead: Lead, ctx: Any) -> List[str]:
    """Content problems of a short text outside the emails (e.g. the personalization line)."""
    text = str(text or "")
    problems = _banned(text, ctx) + _placeholders(text)
    bangs = text.count("!")
    if bangs > MAX_EXCLAMATIONS:
        problems.append(f"{bangs} exclamation marks (max {MAX_EXCLAMATIONS})")
    return problems + _bad_links(text, ctx) + _spam(text) + _caps(text, lead, ctx)


def check_message(message: Message, lead: Lead, ctx: Any, step_index: int, *,
                  signoff: Optional[Tuple[str, str]] = None) -> List[str]:
    """Problems with one email (``step_index`` 0-based). Empty list = passes.

    ``signoff`` = ``(signature, footer)`` if already computed (see
    ``template.signoff_for``); it is stripped before counting words.
    """
    w = ctx.playbook.writer
    try:
        max_words = int(w.get("max_words") or 90)
    except (TypeError, ValueError):
        max_words = 90
    signature, footer = signoff if signoff is not None else signoff_for(lead, ctx)
    body = strip_signoff(message.body or "", signature, footer).strip()
    subject = (message.subject or "").strip()
    text = f"{subject}\n{body}" if subject else body
    problems: List[str] = []

    if not body:
        problems.append("empty body")
    else:
        n = word_count(body)
        if n > max_words:
            problems.append(f"body is {n} words (max {max_words})")

    problems += _banned(text, ctx)
    problems += _placeholders(text)

    if step_index == 0:
        if not subject:
            problems.append("subject is empty")
        else:
            words = len(subject.split())
            if words > MAX_SUBJECT_WORDS:
                problems.append(f"subject is {words} words (max {MAX_SUBJECT_WORDS})")
            if len(subject) > MAX_SUBJECT_CHARS:
                problems.append(f"subject is {len(subject)} characters (max {MAX_SUBJECT_CHARS})")
        needles = _mention_needles(lead)
        if needles and body and not _contains_any(text, needles):
            top = lead.top_signal
            what = f'"{lead.company.name}"' if lead.company.name else "the company"
            if top is not None and top.title:
                what += f' or the signal "{clean_title(top.title) or top.title}"'
            problems.append(f"does not mention {what}")

    bangs = text.count("!")
    if bangs > MAX_EXCLAMATIONS:
        problems.append(f"{bangs} exclamation marks (max {MAX_EXCLAMATIONS})")

    problems += _bad_links(text, ctx)
    problems += _spam(text)
    problems += _caps(text, lead, ctx)

    prefix = f"step {step_index + 1}: "
    return [prefix + p for p in problems]


def check_sequence(messages: List[Message], lead: Lead, ctx: Any) -> List[str]:
    """Problems with a whole sequence (per-message checks + sequence-level checks)."""
    problems: List[str] = []
    expected = len(sequence_steps(ctx))
    if len(messages) != expected:
        problems.append(f"expected {expected} emails (writer.sequence), got {len(messages)}")
    signoff = signoff_for(lead, ctx)
    seen = {}
    for i, msg in enumerate(messages):
        problems += check_message(msg, lead, ctx, i, signoff=signoff)
        key = _norm(strip_signoff(msg.body or "", *signoff))
        if key:
            if key in seen:
                problems.append(f"step {i + 1}: same body as step {seen[key] + 1}")
            else:
                seen[key] = i
    return problems


__all__ = ["check_message", "check_sequence", "check_snippet", "KNOWN_ACRONYMS", "SPAM_PATTERNS"]
