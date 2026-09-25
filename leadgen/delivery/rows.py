"""The client-facing row: column set, honest email labels, "posted X days ago".

This module is the contract between the pipeline's ``Lead`` objects and every
delivery format (CSV, Excel, HTML, Google Sheets). Formats must use
``columns(include_opening)`` for order + headers and must ignore row keys that
start with ``_`` (internal fields used for sorting / the ledger).

Email honesty - ``email_status`` is always exactly one of:

  verified            the mailbox was confirmed deliverable by an email verifier
                      or by the data provider that supplied it (status "valid"),
                      AND the address was not built from a name pattern
  risky               a real address from a provider / import that is not
                      confirmed (catch-all domain, or never checked)
  guessed-unverified  built from a name pattern (first.last@...). ALWAYS this
                      label, whatever a checker said: a guess is never "verified"
  not found           no usable email in this file (none found, it failed
                      verification, or it was withheld by the client's email policy)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from ..models import Contact, EmailStatus, Lead, Signal, SignalType

VERIFIED = "verified"
RISKY = "risky"
GUESSED = "guessed-unverified"
NOT_FOUND = "not found"
EMAIL_LABELS = (VERIFIED, RISKY, GUESSED, NOT_FOUND)

# (key, header) in delivery order. "opening_line" is appended only when enabled.
BASE_COLUMNS: List[Tuple[str, str]] = [
    ("company", "Company"),
    ("website", "Website"),
    ("company_size", "Company size"),
    ("industry", "Industry"),
    ("location", "Location"),
    ("signal_type", "Signal type"),
    ("job_titles", "Job title(s)"),
    ("job_link", "Job link"),
    ("date_posted", "Date posted"),
    ("posted", "Posted"),
    ("urgency", "Urgency"),
    ("score", "Score"),
    ("decision_maker", "Decision-maker"),
    ("decision_maker_title", "Decision-maker title"),
    ("linkedin_url", "LinkedIn URL"),
    ("email", "Email"),
    ("email_status", "Email status"),
    ("source", "Source"),
]
OPENING_COLUMN: Tuple[str, str] = ("opening_line", "Suggested opening line")

SIGNAL_LABELS: Dict[str, str] = {
    SignalType.JOB_POSTING: "Hiring",
    SignalType.FUNDING: "Funding",
    SignalType.LEADERSHIP_CHANGE: "Leadership change",
    SignalType.EXPANSION: "Expansion",
    SignalType.HEADCOUNT_GROWTH: "Headcount growth",
    SignalType.TECH_ADOPTION: "Tech adoption",
    SignalType.NEWS: "News",
    SignalType.REVIEW: "Reviews",
    SignalType.AD_ACTIVITY: "Ad activity",
    SignalType.WEBSITE_CHANGE: "Website change",
    SignalType.EVENT: "Event",
    SignalType.CUSTOM: "Other",
}

MAX_JOB_TITLES = 5


def columns(include_opening: bool = False) -> List[Tuple[str, str]]:
    return BASE_COLUMNS + ([OPENING_COLUMN] if include_opening else [])


def signal_label(signal_type: str) -> str:
    return SIGNAL_LABELS.get(signal_type or "", (signal_type or "Other").replace("_", " ").capitalize())


def is_guessed(contact: Optional[Contact]) -> bool:
    """True when the address was built from a name pattern (never 'verified')."""
    if not contact or not contact.email:
        return False
    if contact.data.get("email_guessed"):
        return True
    if contact.source == "pattern":
        return True
    return contact.email in (contact.email_candidates or [])


def email_label(contact: Optional[Contact]) -> str:
    """One of EMAIL_LABELS for this contact's email (see module docstring)."""
    if not contact or not contact.email:
        return NOT_FOUND
    if contact.email_status == EmailStatus.INVALID:
        return NOT_FOUND
    if is_guessed(contact):
        return GUESSED
    if contact.email_status == EmailStatus.VALID:
        return VERIFIED
    return RISKY


def posted_phrase(signal: Optional[Signal], today: date) -> str:
    """'posted today' / 'posted 1 day ago' / 'posted 5 days ago' / 'date unknown'."""
    if signal is None:
        return ""
    if signal.posted_at is None:
        if signal.first_seen is not None:
            n = max(0, (today - signal.first_seen).days)
            return f"date unknown (first seen {'today' if n == 0 else f'{n} day' + ('s' if n != 1 else '') + ' ago'})"
        return "date unknown"
    n = max(0, (today - signal.posted_at).days)
    if n == 0:
        return "posted today"
    return f"posted {n} day{'s' if n != 1 else ''} ago"


def _job_signals(lead: Lead) -> List[Signal]:
    sigs = list(lead.company.signals or [])
    jobs = [s for s in sigs if s.type == SignalType.JOB_POSTING]
    return jobs or sigs


def build_row(lead: Lead, today: date, include_unverified: bool = True,
              opening_line: str = "", include_opening: bool = False) -> Dict[str, Any]:
    """The client-facing row for one lead (keys = column keys, plus internal ``_`` keys)."""
    c, ct = lead.company, lead.contact
    sigs = _job_signals(lead)
    top = sigs[0] if sigs else None
    titles: List[str] = []
    for s in sigs:
        t = (s.title or "").strip()
        if t and t.lower() not in {x.lower() for x in titles}:
            titles.append(t)
    more = len(titles) - MAX_JOB_TITLES
    job_titles = "; ".join(titles[:MAX_JOB_TITLES]) + (f" (+{more} more)" if more > 0 else "")

    label = email_label(ct)
    email = ct.email if ct and label != NOT_FOUND else ""
    if not include_unverified and label != VERIFIED:
        email, label = "", NOT_FOUND

    src = []
    for s in c.signals or []:
        if s.source and s.source not in src:
            src.append(s.source)
    if not src:
        src = [x for x in c.sources if x]
    source = "; ".join(src)
    if ct and ct.source:
        source = f"{source}; contact via {ct.source}" if source else f"contact via {ct.source}"

    website = c.website or (f"https://{c.domain}" if c.domain else "")
    location = c.location or c.country or (top.location if top else "")
    row: Dict[str, Any] = {
        "company": c.name,
        "website": website,
        "company_size": c.employees if c.employees is not None else "",
        "industry": c.industry or "",
        "location": location or "",
        "signal_type": signal_label(top.type) if top else "",
        "job_titles": job_titles,
        "job_link": top.url if top else "",
        "date_posted": top.posted_at.isoformat() if top and top.posted_at else "",
        "posted": posted_phrase(top, today),
        "urgency": lead.tier,
        "score": lead.score,
        "decision_maker": ct.full_name if ct else "",
        "decision_maker_title": ct.title if ct else "",
        "linkedin_url": ct.linkedin_url if ct else "",
        "email": email,
        "email_status": label,
        "source": source,
        # internal (never written to client files)
        "_lead_id": lead.id,
        "_company_key": c.key,
        "_signal_types": sorted({s.type for s in c.signals or []}),
    }
    if include_opening:
        row["opening_line"] = opening_line or ""
    return row


@dataclass
class DeliveryPackage:
    """Everything a format writer needs for one client delivery."""

    client_name: str                      # machine name (file stem of clients/<name>.yaml)
    client_display: str                   # e.g. "Acme Staffing Ltd"
    period_start: date
    period_end: date                      # the delivery date
    rows: List[Dict[str, Any]]            # build_row() dicts, best first
    include_opening: bool = False
    brand: Dict[str, Any] = field(default_factory=dict)   # playbook.delivery (+ client overrides)
    run_id: str = ""
    notes: List[str] = field(default_factory=list)        # e.g. "freshness: jobs posted in the last 7 days"

    @property
    def columns(self) -> List[Tuple[str, str]]:
        return columns(self.include_opening)

    @property
    def counts_by_signal(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for r in self.rows:
            k = r.get("signal_type") or "Other"
            out[k] = out.get(k, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))

    @property
    def counts_by_email_status(self) -> Dict[str, int]:
        out = {k: 0 for k in EMAIL_LABELS}
        for r in self.rows:
            out[r.get("email_status") or NOT_FOUND] = out.get(r.get("email_status") or NOT_FOUND, 0) + 1
        return out

    @property
    def hot(self) -> int:
        return sum(1 for r in self.rows if r.get("urgency") == "hot")

    def public_rows(self) -> List[List[Any]]:
        """Rows as lists in column order (internal keys dropped)."""
        keys = [k for k, _ in self.columns]
        return [[r.get(k, "") for k in keys] for r in self.rows]
