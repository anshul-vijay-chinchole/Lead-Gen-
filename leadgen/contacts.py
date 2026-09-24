"""Stage 5 - WHO do we talk to? Buyer-title ranking, contact selection and the
contact-finder waterfall.

* ``title_rank(title, ctx)`` - position of a job title in ``buyers.titles``
  (0 = best), None when it is not a target (or is excluded).
* ``is_generic_email(email)`` - role mailboxes (info@, hr@, jobs@, ...).
* ``select_contacts(company, ctx, limit)`` - the best people to email, best
  first.
* ``ContactWaterfall(ctx, errors)`` - runs the playbook's finders in order
  (e.g. Apollo -> Hunter -> pattern guess) until a usable decision-maker is
  found, so paid lookups stop as soon as they are no longer needed.

Title matching uses ``signals.keyword_match`` semantics (whole words, plural
tolerant, case/accent-insensitive) on titles normalised through a small,
generic abbreviation table: ``VP`` = ``Vice President`` (``SVP`` / ``EVP`` are
senior / executive VPs, so they also match a ``VP ...`` buyer title, while
``President`` never matches ``Vice President``); ``CEO`` / ``CFO`` / ``CTO`` /
``COO`` / ``CMO`` / ``CRO`` / ``CHRO`` / ``CPO`` / ``CIO`` / ``CISO`` = ``Chief
... Officer``; ``HR`` = ``Human Resources``; ``TA`` = ``Talent Acquisition``;
``MD`` = ``Managing Director``; ``GM`` = ``General Manager``; ``Dir`` =
``Director``; ``Mgr`` = ``Manager``; ``Sr`` = ``Senior``; ``Ops`` =
``Operations``. Filler words (of, the, and, for) are ignored, so ``VP of
Finance`` = ``VP Finance`` = ``Vice President, Finance``, and a multi-word buyer
title also matches when all of its words appear in another order (``Head of
Finance`` ~ ``Finance Head``, ``Finance Director`` ~ ``Director of Finance``).
Trailing context such as ``at Acme``, ``to the CEO`` or ``reporting to ...`` is
cut off first, so ``Chief of Staff to the CEO`` is not ranked as the CEO.
Known ambiguity: ``CPO`` covers both Chief Product and Chief People Officer.

Playbook keys read: ``buyers.titles``, ``buyers.exclude_titles``,
``buyers.allow_generic_emails``, ``enrichment.finders`` (each
``{type: ..., label?: ..., enabled?: ...}``) and
``enrichment.skip_if_contact_present``.
"""
from __future__ import annotations

import re
from dataclasses import replace
from functools import lru_cache
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import registry
from .context import MissingCredentialError
from .http import HttpError
from .models import Company, Contact, EmailStatus
from .signals import as_str_list, contains_tokens, tokens_equal
from .utils import normalize_text

# --- title normalisation ---------------------------------------------------------

_TITLE_STOPWORDS = frozenset({"of", "the", "and", "for"})

_TITLE_REWRITES: Dict[Tuple[str, ...], Tuple[str, ...]] = {}


def _alias(phrases: Sequence[str], canonical: str) -> None:
    for p in phrases:
        _TITLE_REWRITES[tuple(p.split())] = tuple(canonical.split())


_alias(["chief executive officer", "chief executive"], "ceo")
_alias(["chief financial officer", "chief finance officer"], "cfo")
_alias(["chief technology officer", "chief technical officer"], "cto")
_alias(["chief operating officer", "chief operations officer"], "coo")
_alias(["chief marketing officer"], "cmo")
_alias(["chief revenue officer"], "cro")
_alias(["chief human resources officer", "chief human resource officer", "chief hr officer"], "chro")
_alias(["chief people officer", "chief product officer"], "cpo")
_alias(["chief information officer"], "cio")
_alias(["chief information security officer"], "ciso")
_alias(["vice president", "vice pres", "v p", "vp"], "vp")
_alias(["senior vice president", "svp"], "senior vp")
_alias(["executive vice president", "evp"], "executive vp")
_alias(["assistant vice president", "avp"], "avp")
_alias(["human resources", "human resource", "hr"], "human resources")
_alias(["talent acquisition", "ta"], "talent acquisition")
_alias(["managing director", "md"], "managing director")
_alias(["general manager", "gm"], "general manager")
_alias(["dir"], "director")
_alias(["mgr", "mngr"], "manager")
_alias(["sr", "snr"], "senior")
_alias(["jr"], "junior")
_alias(["ops"], "operations")
_alias(["asst"], "assistant")
_alias(["exec"], "executive")
_alias(["mktg"], "marketing")
_alias(["cofounder"], "co founder")
_alias(["biz dev", "bizdev"], "business development")

_MAX_REWRITE = max(len(k) for k in _TITLE_REWRITES)

# Context that is not part of the role: "VP Sales at Acme", "Chief of Staff to the CEO".
_TITLE_CUTS = (
    re.compile(r"\s+(?:at|@)\s+.*$", re.IGNORECASE),
    re.compile(r"\b(?:reporting|reports)\s+(?:directly\s+)?to\b.*$", re.IGNORECASE),
    re.compile(r"\b(?:to|for)\s+the\s+.*$", re.IGNORECASE),
    re.compile(r"\bto\s+(?:ceo|cfo|coo|cto|cmo|cro|md|founders?|president|chairman|chief)\b.*$",
               re.IGNORECASE),
)


@lru_cache(maxsize=8192)
def canonical_title(title: str) -> Tuple[str, ...]:
    """Normalised title tokens: 'VP of Finance' -> ('vp', 'finance')."""
    raw = str(title or "")
    for cut in _TITLE_CUTS:
        shorter = cut.sub("", raw).strip()
        if shorter:
            raw = shorter
    tokens = [t for t in normalize_text(raw).split() if t not in _TITLE_STOPWORDS]
    out: List[str] = []
    i = 0
    while i < len(tokens):
        for size in range(min(_MAX_REWRITE, len(tokens) - i), 0, -1):
            rep = _TITLE_REWRITES.get(tuple(tokens[i:i + size]))
            if rep is not None:
                out.extend(rep)
                i += size
                break
        else:
            out.append(tokens[i])
            i += 1
    return tuple(out)


def title_matches(title: str, wanted: str) -> bool:
    """True if job ``title`` is an instance of buyer title ``wanted``."""
    have, want = canonical_title(title), canonical_title(wanted)
    if not have or not want:
        return False
    if contains_tokens(have, want):
        return True
    if len(want) >= 2:  # same words, different order: "Director of Finance" ~ "Finance Director"
        return all(any(tokens_equal(h, w) for h in have) for w in want)
    return False


def is_excluded_title(title: str, ctx: Any) -> bool:
    """True if ``title`` mentions one of ``buyers.exclude_titles`` (intern, assistant, ...)."""
    have = canonical_title(str(title or ""))
    if not have:
        return False
    for bad in as_str_list(ctx.playbook.buyers.get("exclude_titles")):
        want = canonical_title(bad)
        if want and contains_tokens(have, want):
            return True
    return False


def title_rank(title: str, ctx: Any) -> Optional[int]:
    """Index of the first ``buyers.titles`` entry ``title`` matches (0 = best buyer).

    None when the title is empty, excluded or matches no entry. When
    ``buyers.titles`` is empty every non-excluded title ranks 0.
    """
    if not title or not str(title).strip():
        return None
    title = str(title)
    if is_excluded_title(title, ctx):
        return None
    wanted = as_str_list(ctx.playbook.buyers.get("titles"))
    if not wanted:
        return 0
    for i, w in enumerate(wanted):
        if title_matches(title, w):
            return i
    return None


# --- generic mailboxes -----------------------------------------------------------------

GENERIC_LOCAL_PARTS = frozenset({
    "info", "information", "hello", "hi", "hey", "contact", "contactus", "contacts", "sales",
    "admin", "administrator", "administration", "office", "support", "help", "helpdesk",
    "hr", "humanresources", "jobs", "job", "careers", "career", "recruitment", "recruiting",
    "recruiter", "recruit", "talent", "hiring", "people", "team", "enquiries", "enquiry",
    "inquiries", "inquiry", "mail", "email", "post", "billing", "accounts", "accounting",
    "finance", "invoices", "invoice", "payments", "payroll", "marketing", "press", "media",
    "pr", "news", "newsletter", "noreply", "donotreply", "donotrespond", "mailer", "mailerdaemon",
    "postmaster", "webmaster", "hostmaster", "abuse", "privacy", "legal", "compliance",
    "security", "service", "services", "customerservice", "customerservices", "customercare",
    "care", "reception", "frontdesk", "general", "feedback", "orders", "order", "shop", "store",
    "partners", "partnerships", "events", "bookings", "booking", "reservations", "it",
    "operations", "ops", "welcome", "all", "staff", "company", "biz", "business", "enquire",
})

_LOCAL_SPLIT = re.compile(r"[._+\-]+")


def is_generic_email(email: Any) -> bool:
    """True for role mailboxes (info@, hello@, hr@, jobs@, no-reply@, sales.uk@, info2@ ...)."""
    if not email or "@" not in str(email):
        return False
    local = str(email).strip().lower().split("@", 1)[0]
    if not local:
        return False
    candidates = {local, _LOCAL_SPLIT.sub("", local)}
    parts = [p for p in _LOCAL_SPLIT.split(local) if p]
    if parts:
        candidates.add(parts[0])
    for c in list(candidates):
        candidates.add(c.rstrip("0123456789"))
    return any(c in GENERIC_LOCAL_PARTS for c in candidates if c)


# --- selection ------------------------------------------------------------------------------

_STATUS_ORDER = {EmailStatus.VALID: 0, EmailStatus.RISKY: 1, EmailStatus.UNKNOWN: 2, EmailStatus.INVALID: 3}


def _allow_generic(ctx: Any) -> bool:
    return bool(ctx.playbook.buyers.get("allow_generic_emails", False))


def _usable_email(email: str, ctx: Any) -> bool:
    return bool(email) and (_allow_generic(ctx) or not is_generic_email(email))


def _is_named(contact: Contact) -> bool:
    return bool((contact.full_name or "").strip() or contact.first_name or contact.last_name)


def _rank_key(contact: Contact, rank: Optional[int]) -> Tuple[int, int, int, int, float]:
    return (
        0 if rank is not None else 1,
        rank if rank is not None else 0,
        0 if contact.email else 1,
        _STATUS_ORDER.get(contact.email_status, 2) if contact.email else 4,
        -(contact.confidence or 0.0),
    )


def select_contacts(company: Company, ctx: Any, limit: Optional[int] = 1) -> List[Contact]:
    """The best people at ``company`` to email, best first, at most ``limit``.

    Drops excluded titles and generic mailboxes (unless
    ``buyers.allow_generic_emails``; a *named* person with a generic address
    is kept without it). Ranks buyer titles first (by priority), then other
    titles; ties prefer people with an email, then valid > risky > unknown >
    invalid, then higher provider confidence. Duplicates (same
    ``Contact.key``) keep the best-ranked record. ``company`` is not mutated:
    stripped contacts are copies, all others are the original objects.
    """
    if limit is not None and limit <= 0:
        return []
    allow_generic = _allow_generic(ctx)
    pool: List[Tuple[Tuple[int, int, int, int, float], int, Contact]] = []
    for idx, contact in enumerate(company.contacts or []):
        if not isinstance(contact, Contact):
            continue
        if contact.title and is_excluded_title(contact.title, ctx):
            continue
        if not allow_generic:
            generic_email = is_generic_email(contact.email)
            if generic_email and not _is_named(contact):
                continue  # a role mailbox, not a person
            if generic_email or any(is_generic_email(e) for e in contact.email_candidates):
                contact = replace(
                    contact,
                    email="" if generic_email else contact.email,
                    email_status=EmailStatus.UNKNOWN if generic_email else contact.email_status,
                    email_candidates=[e for e in contact.email_candidates if not is_generic_email(e)],
                    data=dict(contact.data),
                )
        if not contact.key:
            continue
        pool.append((_rank_key(contact, title_rank(contact.title, ctx)), idx, contact))
    pool.sort(key=lambda t: (t[0], t[1]))
    out: List[Contact] = []
    seen = set()
    for _, _, contact in pool:
        if contact.key in seen:
            continue
        seen.add(contact.key)
        out.append(contact)
        if limit is not None and len(out) >= limit:
            break
    return out


# --- the finder waterfall ----------------------------------------------------------------------

def _norm_url(url: str) -> str:
    return (url or "").strip().rstrip("/").lower().replace("http://", "https://").replace("://www.", "://")


def same_person(a: Contact, b: Contact) -> bool:
    """Heuristic identity: same key, LinkedIn URL, email, or (no conflicting ids and) same name."""
    if a.key and a.key == b.key:
        return True
    la, lb = _norm_url(a.linkedin_url), _norm_url(b.linkedin_url)
    if la and lb:
        return la == lb
    if a.email and b.email:
        return a.email == b.email
    na, nb = normalize_text(a.full_name), normalize_text(b.full_name)
    return bool(na) and na == nb


def fill_contact(target: Contact, source: Contact) -> None:
    """Fill ``target``'s empty fields from ``source`` (never overwrites)."""
    if not target.email and source.email:
        target.email = source.email
        target.email_status = source.email_status
    for attr in ("title", "linkedin_url", "full_name", "first_name", "last_name", "phone",
                 "seniority", "department", "location", "source"):
        if not getattr(target, attr) and getattr(source, attr):
            setattr(target, attr, getattr(source, attr))
    if target.confidence is None and source.confidence is not None:
        target.confidence = source.confidence
    for e in source.email_candidates:
        if e and e != target.email and e not in target.email_candidates:
            target.email_candidates.append(e)
    for k, v in (source.data or {}).items():
        target.data.setdefault(k, v)


def merge_contacts(company: Company, found: Sequence[Contact]) -> int:
    """Add ``found`` people to ``company.contacts``; returns how many were new."""
    added = 0
    for c in found:
        existing = next((e for e in company.contacts if isinstance(e, Contact) and same_person(e, c)), None)
        if existing is None:
            company.contacts.append(c)
            added += 1
        else:
            fill_contact(existing, c)
    return added


class _Slot:
    """One configured finder in the waterfall."""

    def __init__(self, label: str, finder: Any) -> None:
        self.label = label
        self.finder = finder
        self.disabled = ""  # reason, once credentials/plan failures make retries pointless


class ContactWaterfall:
    """Run ``enrichment.finders`` in order until the company has a usable decision-maker.

    Finders are built once (``registry.create('finder', cfg, ctx)``). A finder
    config with ``enabled: false`` is skipped; an unknown type or a finder that
    fails to build is reported in ``errors`` and skipped; in ``ctx.dry_run``
    finders that use the network (``offline = False``) are skipped.

    ``enrich(company)`` does nothing when ``enrichment.skip_if_contact_present``
    is on and the company already has a target-title contact with a usable
    (non-generic, not invalid) email. Otherwise each finder in turn:
    ``find(company)`` -> new people are merged into ``company.contacts`` (a
    known person only gets missing fields filled); then ``complete(company,
    contact)`` is called for target-title contacts that still lack an email,
    best-ranked first. The waterfall stops as soon as a target-title contact
    (any contact when ``buyers.titles`` is empty) has a usable email or email
    candidates to verify. Finder errors are appended to ``errors`` as
    ``"finder <label>: <message>"`` and the next finder runs; after a missing
    credential or an HTTP 401/402/403 the finder is disabled for the rest of
    the run (it would fail the same way for every company).
    """

    DISABLE_STATUSES = (401, 402, 403)

    def __init__(self, ctx: Any, errors: Optional[list] = None) -> None:
        self.ctx = ctx
        self.errors: list = errors if errors is not None else []
        self.slots: List[_Slot] = []
        for cfg in ctx.playbook.enrichment.get("finders") or []:
            if not isinstance(cfg, dict) or cfg.get("enabled") is False:
                continue
            label = str(cfg.get("label") or cfg.get("type") or "?")
            try:
                finder = registry.create("finder", cfg, ctx)
            except Exception as e:  # noqa: BLE001 - unknown type, import error, bad config
                self._error(label, e)
                continue
            if ctx.dry_run and not getattr(finder, "offline", False):
                ctx.log.info("dry-run: skipping finder '%s' (uses the network)", label)
                continue
            self.slots.append(_Slot(label, finder))

    @property
    def finders(self) -> List[Any]:
        """The finder instances that will run, in order."""
        return [s.finder for s in self.slots]

    @property
    def names(self) -> List[str]:
        return [s.label for s in self.slots]

    def _error(self, label: str, exc: Exception) -> None:
        text = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
        msg = f"finder {label}: {text or type(exc).__name__}"
        self.errors.append(msg)
        self.ctx.log.error(msg)

    # --- predicates -------------------------------------------------------------
    def _is_target(self, contact: Contact) -> bool:
        if contact.title and is_excluded_title(contact.title, self.ctx):
            return False
        if not as_str_list(self.ctx.playbook.buyers.get("titles")):
            return True
        return title_rank(contact.title, self.ctx) is not None

    def _reachable(self, contact: Contact, with_candidates: bool) -> bool:
        if contact.email and contact.email_status != EmailStatus.INVALID and _usable_email(contact.email, self.ctx):
            return True
        if with_candidates:
            return any(_usable_email(e, self.ctx) for e in contact.email_candidates)
        return False

    def satisfied(self, company: Company, with_candidates: bool = True) -> bool:
        """True when a target contact is reachable (usable email, or candidates to verify)."""
        return any(self._is_target(c) and self._reachable(c, with_candidates)
                   for c in company.contacts if isinstance(c, Contact))

    # --- the waterfall ------------------------------------------------------------
    def _to_contacts(self, found: Any, label: str) -> List[Contact]:
        out: List[Contact] = []
        for item in found or []:
            if isinstance(item, dict):
                item = Contact.from_dict(item)
            if not isinstance(item, Contact):
                self.ctx.log.warning("finder %s returned a non-contact: %r", label, item)
                continue
            if not item.source:
                item.source = label
            out.append(item)
        return out

    def _complete(self, slot: _Slot, company: Company) -> None:
        pending = [c for c in company.contacts
                   if isinstance(c, Contact) and not c.email and self._is_target(c)]

        def rank_of(c: Contact) -> int:
            r = title_rank(c.title, self.ctx)
            return r if r is not None else 10 ** 6

        pending.sort(key=rank_of)
        for contact in pending:
            result = slot.finder.complete(company, contact)
            if isinstance(result, Contact) and result is not contact:
                fill_contact(contact, result)
            if self.satisfied(company):
                break

    def enrich(self, company: Company) -> List[str]:
        """Run the waterfall for one company. Returns the labels of the finders called."""
        if self.ctx.playbook.enrichment.get("skip_if_contact_present", True) and \
                self.satisfied(company, with_candidates=False):
            self.ctx.log.debug("enrich %s: already has a reachable decision-maker", company.name)
            return []
        ran: List[str] = []
        for slot in self.slots:
            if slot.disabled:
                continue
            ran.append(slot.label)
            try:
                found = self._to_contacts(slot.finder.find(company), slot.label)
                added = merge_contacts(company, found)
                self.ctx.log.info("finder %s: %d people for %s (%d new)", slot.label, len(found),
                                  company.name, added)
                if not self.satisfied(company):
                    self._complete(slot, company)
            except MissingCredentialError as e:
                self._error(slot.label, e)
                slot.disabled = str(e)
            except HttpError as e:
                self._error(slot.label, e)
                if e.status in self.DISABLE_STATUSES:
                    slot.disabled = str(e)
            except Exception as e:  # noqa: BLE001 - one broken provider must not stop the rest
                self._error(slot.label, e)
            if slot.disabled:
                self.ctx.log.warning("finder %s disabled for the rest of this run", slot.label)
            if self.satisfied(company):
                break
        return ran


__all__ = [
    "ContactWaterfall", "GENERIC_LOCAL_PARTS", "canonical_title", "fill_contact", "is_excluded_title",
    "is_generic_email", "merge_contacts", "same_person", "select_contacts", "title_matches",
    "title_rank",
]
