"""Core data model shared by every stage of the pipeline.

Everything is a plain dataclass so adapters stay simple: a source produces
``Company`` objects (optionally already carrying ``Signal``s and ``Contact``s),
enrichment adds ``Contact``s, the verifier sets ``Contact.email_status``,
scoring turns a company + chosen contact into a ``Lead`` and the writer fills
``Lead.messages``.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

from .utils import company_key, normalize_domain, normalize_text, parse_date, to_int


# --- vocabularies -----------------------------------------------------------

class SignalType:
    """Well-known signal types. Any other string is allowed too (custom signals)."""

    JOB_POSTING = "job_posting"
    FUNDING = "funding"
    LEADERSHIP_CHANGE = "leadership_change"
    EXPANSION = "expansion"
    HEADCOUNT_GROWTH = "headcount_growth"
    TECH_ADOPTION = "tech_adoption"
    NEWS = "news"
    REVIEW = "review"
    AD_ACTIVITY = "ad_activity"
    WEBSITE_CHANGE = "website_change"
    EVENT = "event"
    CUSTOM = "custom"

    ALL = (
        JOB_POSTING, FUNDING, LEADERSHIP_CHANGE, EXPANSION, HEADCOUNT_GROWTH,
        TECH_ADOPTION, NEWS, REVIEW, AD_ACTIVITY, WEBSITE_CHANGE, EVENT, CUSTOM,
    )


class EmailStatus:
    UNKNOWN = "unknown"      # not checked, or checker could not decide
    VALID = "valid"          # deliverable
    RISKY = "risky"          # catch-all / accept-all domain
    INVALID = "invalid"      # will bounce, disposable, spamtrap, ...

    ALL = (UNKNOWN, VALID, RISKY, INVALID)


class Tier:
    HOT = "hot"
    NORMAL = "normal"
    SKIP = "skip"


class Stage:
    """Funnel stages a lead moves through (stored in the DB for stats)."""

    SOURCED = "sourced"
    QUALIFIED = "qualified"      # passed ICP filter
    ENRICHED = "enriched"        # decision-maker found
    VERIFIED = "verified"        # email verified (valid or accepted risky)
    READY = "ready"              # scored + written, not yet sent
    EXPORTED = "exported"        # handed to a sending tool
    REPLIED = "replied"
    POSITIVE = "positive"
    BOOKED = "booked"
    WON = "won"
    LOST = "lost"

    ORDER = (SOURCED, QUALIFIED, ENRICHED, VERIFIED, READY, EXPORTED,
             REPLIED, POSITIVE, BOOKED, WON)
    ALL = ORDER + (LOST,)


class ReplyCategory:
    POSITIVE = "positive"        # interested / wants info / wants a call
    REFERRAL = "referral"        # "talk to Jane instead"
    TIMING = "timing"            # "not now, try next quarter"
    QUESTION = "question"        # asks something before deciding
    NEGATIVE = "negative"        # not interested
    UNSUBSCRIBE = "unsubscribe"  # remove me
    OOO = "ooo"                  # auto-reply out of office
    BOUNCE = "bounce"            # delivery failure
    OTHER = "other"

    ALL = (POSITIVE, REFERRAL, TIMING, QUESTION, NEGATIVE, UNSUBSCRIBE, OOO, BOUNCE, OTHER)


# --- entities ---------------------------------------------------------------

@dataclass
class Signal:
    """A reason a company might buy *now* (a job post, funding round, ...)."""

    type: str
    title: str
    source: str = ""
    posted_at: Optional[date] = None
    url: str = ""
    location: str = ""
    description: str = ""
    external_id: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    # derived by the signals stage
    reposted: bool = False
    first_seen: Optional[date] = None

    def __post_init__(self) -> None:
        self.posted_at = parse_date(self.posted_at)
        self.first_seen = parse_date(self.first_seen)

    def age_days(self, today: date) -> Optional[int]:
        ref = self.posted_at or self.first_seen
        if ref is None:
            return None
        return max(0, (today - ref).days)

    @property
    def fingerprint(self) -> str:
        """Stable identity used for dedupe/repost detection (type + normalized title)."""
        return f"{self.type}:{normalize_text(self.title)}"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["posted_at"] = self.posted_at.isoformat() if self.posted_at else None
        d["first_seen"] = self.first_seen.isoformat() if self.first_seen else None
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Signal":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Contact:
    """A person at a company."""

    first_name: str = ""
    last_name: str = ""
    full_name: str = ""
    title: str = ""
    email: str = ""
    email_status: str = EmailStatus.UNKNOWN
    email_candidates: List[str] = field(default_factory=list)  # guesses to verify, in order
    linkedin_url: str = ""
    phone: str = ""
    seniority: str = ""
    department: str = ""
    location: str = ""
    source: str = ""
    confidence: Optional[float] = None  # 0..1 provider confidence, if any
    data: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.email = (self.email or "").strip().lower()
        if not self.full_name and (self.first_name or self.last_name):
            self.full_name = f"{self.first_name} {self.last_name}".strip()
        if self.full_name and not (self.first_name or self.last_name):
            parts = self.full_name.split()
            self.first_name = parts[0]
            self.last_name = " ".join(parts[1:])
        if self.email_status not in EmailStatus.ALL:
            self.email_status = EmailStatus.UNKNOWN

    @property
    def key(self) -> str:
        if self.email:
            return self.email
        if self.linkedin_url:
            return self.linkedin_url.rstrip("/").lower()
        return normalize_text(self.full_name)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Contact":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Company:
    """An account. ``signals`` and ``contacts`` accumulate as stages run."""

    name: str
    domain: str = ""
    website: str = ""
    linkedin_url: str = ""
    location: str = ""
    country: str = ""
    industry: str = ""
    employees: Optional[int] = None
    description: str = ""
    keywords: List[str] = field(default_factory=list)
    signals: List[Signal] = field(default_factory=list)
    contacts: List[Contact] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    data: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.name = (self.name or "").strip()
        self.domain = normalize_domain(self.domain or self.website)
        if self.employees is not None and not isinstance(self.employees, int):
            self.employees = to_int(self.employees)

    @property
    def key(self) -> str:
        return company_key(self.name, self.domain)

    def merge(self, other: "Company") -> None:
        """Merge another record for the same company into this one (in place)."""
        for attr in ("domain", "website", "linkedin_url", "location", "country",
                     "industry", "description"):
            if not getattr(self, attr) and getattr(other, attr):
                setattr(self, attr, getattr(other, attr))
        if self.employees is None:
            self.employees = other.employees
        if not self.name and other.name:
            self.name = other.name
        for kw in other.keywords:
            if kw not in self.keywords:
                self.keywords.append(kw)
        seen_sig = {(s.fingerprint, s.external_id) for s in self.signals}
        for s in other.signals:
            if (s.fingerprint, s.external_id) not in seen_sig:
                self.signals.append(s)
                seen_sig.add((s.fingerprint, s.external_id))
        seen_c = {c.key for c in self.contacts}
        for c in other.contacts:
            if c.key not in seen_c:
                self.contacts.append(c)
                seen_c.add(c.key)
        for src in other.sources:
            if src not in self.sources:
                self.sources.append(src)
        for k, v in other.data.items():
            self.data.setdefault(k, v)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["signals"] = [s.to_dict() for s in self.signals]
        d["contacts"] = [c.to_dict() for c in self.contacts]
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Company":
        d = dict(d)
        signals = [Signal.from_dict(s) for s in d.pop("signals", []) or []]
        contacts = [Contact.from_dict(c) for c in d.pop("contacts", []) or []]
        c = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        c.signals, c.contacts = signals, contacts
        return c


@dataclass
class Message:
    """One step of an outbound sequence."""

    step: int
    day: int
    subject: str
    body: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ScoreBreakdown:
    intent: float = 0.0        # how strong / fresh the buying signal is
    fit: float = 0.0           # how well the company matches the ICP
    reachability: float = 0.0  # right person + deliverable email
    extra: float = 0.0         # secondary signals (funding, growth, ...)
    reasons: List[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return int(round(max(0.0, min(100.0, self.intent + self.fit + self.reachability + self.extra))))

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["total"] = self.total
        return d


@dataclass
class Lead:
    """A company + the chosen decision-maker, scored and (optionally) written."""

    company: Company
    contact: Optional[Contact] = None
    score: int = 0
    tier: str = Tier.SKIP
    breakdown: ScoreBreakdown = field(default_factory=ScoreBreakdown)
    messages: List[Message] = field(default_factory=list)
    personalization: str = ""      # the one-line opener used in email 1
    hypothesis: str = ""           # why we think they have the problem
    writer: str = ""               # which writer produced the copy
    stage: str = Stage.SOURCED
    playbook: str = ""
    run_id: str = ""
    notes: List[str] = field(default_factory=list)  # why skipped / warnings, shown in exports

    @property
    def id(self) -> str:
        who = self.contact.key if self.contact else ""
        raw = f"{self.playbook}|{self.company.key}|{who}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    @property
    def top_signal(self) -> Optional[Signal]:
        """Most recent primary-looking signal (first after the signals stage sorts them)."""
        return self.company.signals[0] if self.company.signals else None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "company": self.company.to_dict(),
            "contact": self.contact.to_dict() if self.contact else None,
            "score": self.score,
            "tier": self.tier,
            "breakdown": self.breakdown.to_dict(),
            "messages": [m.to_dict() for m in self.messages],
            "personalization": self.personalization,
            "hypothesis": self.hypothesis,
            "writer": self.writer,
            "stage": self.stage,
            "playbook": self.playbook,
            "run_id": self.run_id,
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Lead":
        bd = dict(d.get("breakdown") or {})
        bd.pop("total", None)
        return cls(
            company=Company.from_dict(d["company"]),
            contact=Contact.from_dict(d["contact"]) if d.get("contact") else None,
            score=int(d.get("score", 0)),
            tier=d.get("tier", Tier.SKIP),
            breakdown=ScoreBreakdown(**bd),
            messages=[Message(**m) for m in d.get("messages", [])],
            personalization=d.get("personalization", ""),
            hypothesis=d.get("hypothesis", ""),
            writer=d.get("writer", ""),
            stage=d.get("stage", Stage.SOURCED),
            playbook=d.get("playbook", ""),
            run_id=d.get("run_id", ""),
            notes=list(d.get("notes") or []),
        )


@dataclass
class Reply:
    """An inbound reply to one of our emails, plus its classification."""

    from_email: str
    body: str
    subject: str = ""
    received_at: str = ""
    lead_id: str = ""
    category: str = ReplyCategory.OTHER
    confidence: float = 0.0
    summary: str = ""
    referral_name: str = ""
    referral_email: str = ""
    follow_up_date: Optional[str] = None   # ISO date to re-contact (timing / ooo)
    suggested_reply: str = ""
    action: str = ""
    classifier: str = ""
    data: Dict[str, Any] = field(default_factory=dict)  # provider ids, bounced address, ...

    def __post_init__(self) -> None:
        self.from_email = (self.from_email or "").strip().lower()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
