"""The delivery ledger: what each client has already received, plus each
client's own do-not-list.

A client pays for *new* leads every week, so nothing may be delivered twice:
the ledger remembers every delivered company, job and contact per client, and
``LedgerHooks`` removes them from the next run right after the ICP filter -
before any paid contact lookup is spent on them.

Two tables live in the same SQLite file as the ``Store`` (created on first
use, through ``store.conn``)::

    deliveries(client, kind, key, run_id, delivered_at)       PRIMARY KEY (client, kind, key)
    client_suppression(client, kind, value, reason, added_at) PRIMARY KEY (client, kind, value)

Item kinds and their keys (the same functions build the keys when recording
and when checking, so they always agree):

``company``  ``company_item_key(company)`` = ``company.key`` (the domain, else
             ``name:<normalised name>``)
``job``      ``job_item_key(company, signal)``: ``<company.key>|<external_id>``;
             else the job URL (scheme, ``www.``, fragment and tracking
             parameters such as ``utm_*`` removed - parameters that identify
             the job, like Indeed's ``?jk=``, are kept so two jobs on the
             same board never collide); else ``<company.key>|<fingerprint>``
             (type + normalised title)
``contact``  ``contact_item_key(contact)``: the email, else the normalised
             LinkedIn URL, else ``<normalised name>|<company.key>``

Suppression kinds: email, domain, company, linkedin (values normalised with
``store.normalize_suppression``). A suppressed domain also covers its
subdomains and every email address at it.

Client settings read by ``LedgerHooks`` (from ``delivery.client.Client``):
``dedupe`` (which kinds are de-duplicated), ``redelivery_days`` (None = never
again; N = allowed again once N days have passed) and
``exclusions.companies`` / ``exclusions.domains`` (the client file's own
do-not-list, applied on top of the stored client suppression list; they are
not stored, so deleting a name from the client file lifts it).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit

from ..models import Company, Contact, Lead, Signal, SignalType
from ..pipeline import PipelineHooks
from ..store import SUPPRESSION_KINDS, normalize_suppression
from ..utils import normalize_company_name, normalize_domain, normalize_text

DELIVERY_KINDS = ("company", "job", "contact")

SCHEMA = """
CREATE TABLE IF NOT EXISTS deliveries (
    client TEXT NOT NULL,
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    run_id TEXT,
    delivered_at TEXT NOT NULL,
    PRIMARY KEY (client, kind, key)
);
CREATE INDEX IF NOT EXISTS idx_deliveries_client ON deliveries(client, delivered_at);
CREATE TABLE IF NOT EXISTS client_suppression (
    client TEXT NOT NULL,
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    reason TEXT,
    added_at TEXT NOT NULL,
    PRIMARY KEY (client, kind, value)
);
"""

# Query parameters that only track where a click came from (never identify a job).
_TRACKING_PARAM = re.compile(
    r"^(?:utm_\w*|gclid|gclsrc|dclid|fbclid|msclkid|yclid|mc_cid|mc_eid|_hsenc|_hsmi|hsa_\w*|"
    r"ref|ref_src|refid|referer|referrer|trk|trkinfo|trackingid|tracking_id|source|src|from|"
    r"sessionid|session_id|sid|campaign|campaignid|clickid|click_id)$",
    re.IGNORECASE)
_DOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$")


def _now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat()


def _client_name(client: Any) -> str:
    """Accept a ``Client`` or its name."""
    name = str(getattr(client, "name", client) or "").strip()
    if not name:
        raise ValueError("client name is empty")
    return name


def _check_kind(kind: str) -> str:
    k = str(kind or "").strip().lower()
    if k not in DELIVERY_KINDS:
        raise ValueError(f"delivery kind must be one of {', '.join(DELIVERY_KINDS)} (got {kind!r})")
    return k


# --- item keys -------------------------------------------------------------------------

def company_item_key(company: Company) -> str:
    """Ledger key of a company (= ``company.key``)."""
    return company.key


def normalize_job_url(url: str) -> str:
    """A job URL reduced to what identifies the job: no scheme, ``www.``,
    fragment, trailing slash or tracking parameters; other parameters sorted."""
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw if "://" in raw else "https://" + raw)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:  # malformed URL (bad port, stray brackets): use it as plain text
        return raw.split("#", 1)[0].strip().lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return ""
    query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                   if not _TRACKING_PARAM.match(k))
    out = host + (f":{port}" if port else "") + parts.path.rstrip("/")
    return out + (f"?{urlencode(query)}" if query else "")


def job_item_key(company: Company, signal: Signal) -> str:
    """Ledger key of one job (signal) at a company - see the module docstring."""
    ext = str(signal.external_id or "").strip()
    if ext:
        return f"{company.key}|{ext}"
    url = normalize_job_url(signal.url)
    if url:
        return url
    return f"{company.key}|{signal.fingerprint}"


def contact_item_key(contact: Contact, company: Optional[Company] = None) -> str:
    """Ledger key of a person: the email, else the normalised LinkedIn URL,
    else ``<normalised name>|<company key>`` ('' when there is nothing to go on)."""
    keys = contact_item_keys(contact, company)
    return keys[0] if keys else ""


def contact_item_keys(contact: Contact, company: Optional[Company] = None) -> List[str]:
    """Every identity of a person that the ledger can match (email, LinkedIn, name),
    best first. Checking all of them catches someone delivered under one identity
    and found again under another (e.g. first without, later with an email)."""
    out: List[str] = []
    email = str(contact.email or "").strip().lower()
    if email:
        out.append(email)
    if contact.linkedin_url:
        li = normalize_suppression(contact.linkedin_url, "linkedin")
        if li:
            out.append(li)
    name = normalize_text(contact.full_name or f"{contact.first_name} {contact.last_name}")
    if name:
        out.append(f"{name}|{company.key if company is not None else ''}")
    return list(dict.fromkeys(out))


def lead_items(lead: Lead) -> List[Tuple[str, str]]:
    """The (kind, key) items a delivered lead puts in the ledger: its company,
    every signal (job) it was delivered with, and every identity of its contact."""
    c = lead.company
    items: List[Tuple[str, str]] = [("company", company_item_key(c))]
    items += [("job", job_item_key(c, s)) for s in c.signals or []]
    if lead.contact is not None:
        items += [("contact", k) for k in contact_item_keys(lead.contact, c)]
    return list(dict.fromkeys(i for i in items if i[1]))


# --- suppression helpers ----------------------------------------------------------------

def guess_kind(value: str) -> str:
    """Suppression kind for a value typed by a person: email, linkedin, domain or company."""
    v = str(value or "").strip().lower()
    if "@" in v and "/" not in v:
        return "email"
    if "linkedin.com/" in v:
        return "linkedin"
    if " " not in v and _DOMAIN_RE.match(normalize_domain(v) or "-"):
        return "domain"
    return "company"


def domain_and_parents(domain: str) -> List[str]:
    """'eu.jobs.acme.com' -> ['eu.jobs.acme.com', 'jobs.acme.com', 'acme.com']."""
    d = normalize_domain(domain)
    labels = [x for x in d.split(".") if x]
    return [".".join(labels[i:]) for i in range(0, max(0, len(labels) - 1))]


# --- the ledger ---------------------------------------------------------------------------

class Ledger:
    """Per-client delivery history + per-client suppression list (see module docstring).

    ``store`` is a ``leadgen.store.Store`` (anything with a sqlite3 ``conn``).
    Dates are the delivery day (``today``, default: the system date).
    """

    def __init__(self, store: Any) -> None:
        self.store = store
        self._ready = False

    @property
    def conn(self) -> Any:
        """The store's connection; the ledger tables are created on first use."""
        conn = self.store.conn
        if not self._ready:
            conn.executescript(SCHEMA)
            conn.commit()
            self._ready = True
        return conn

    # --- deliveries -------------------------------------------------------------------
    def delivered_on(self, client: Any, kind: str, key: str, window_days: Optional[int] = None,
                     today: Optional[date] = None) -> Optional[date]:
        """The day this item was (last) delivered to the client, or None.

        ``window_days`` None = ever delivered; N = only a delivery less than N
        days before ``today`` counts (after N days the item may be delivered again).
        """
        k = str(key or "").strip()
        if not k:
            return None
        args: List[Any] = [_client_name(client), _check_kind(kind), k]
        q = "SELECT delivered_at FROM deliveries WHERE client=? AND kind=? AND key=?"
        if window_days is not None:
            cutoff = (today or date.today()) - timedelta(days=max(0, int(window_days)))
            q += " AND delivered_at > ?"
            args.append(cutoff.isoformat())
        row = self.conn.execute(q, args).fetchone()
        return date.fromisoformat(row["delivered_at"][:10]) if row else None

    def delivered(self, client: Any, kind: str, key: str, window_days: Optional[int] = None,
                  today: Optional[date] = None) -> bool:
        """True if this item was delivered to the client (within the window, see ``delivered_on``)."""
        return self.delivered_on(client, kind, key, window_days, today) is not None

    def record(self, client: Any, items: Iterable[Tuple[str, str]], run_id: str = "",
               today: Optional[date] = None) -> int:
        """Remember that these (kind, key) items were delivered today. Idempotent:
        recording an item again moves it to this run / day. Empty keys are
        ignored. Returns the number of distinct items recorded."""
        name = _client_name(client)
        day = (today or date.today()).isoformat()
        rows: Dict[Tuple[str, str], None] = {}
        for kind, key in items:
            k = str(key or "").strip()
            if k:
                rows[(_check_kind(kind), k)] = None
        conn = self.conn
        conn.executemany(
            "INSERT INTO deliveries (client, kind, key, run_id, delivered_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(client, kind, key) DO UPDATE SET run_id=excluded.run_id, "
            "delivered_at=excluded.delivered_at",
            [(name, kind, key, str(run_id or ""), day) for kind, key in rows])
        conn.commit()
        return len(rows)

    def summary(self, client: Any) -> Dict[str, Any]:
        """What the client has received so far::

            {"client", "deliveries" (number of delivery runs), "first_delivery",
             "last_delivery" (YYYY-MM-DD or None), "items", "company", "job",
             "contact", "by_kind": {kind: n}, "suppressed" (do-not-list entries)}
        """
        name = _client_name(client)
        conn = self.conn
        by_kind = {k: 0 for k in DELIVERY_KINDS}
        for r in conn.execute("SELECT kind, COUNT(*) AS n FROM deliveries WHERE client=? GROUP BY kind",
                              (name,)):
            by_kind[r["kind"]] = r["n"]
        row = conn.execute(
            "SELECT COUNT(DISTINCT CASE WHEN run_id IS NULL OR run_id='' THEN 'day:' || delivered_at "
            "ELSE run_id END) AS runs, MIN(delivered_at) AS first, MAX(delivered_at) AS last "
            "FROM deliveries WHERE client=?", (name,)).fetchone()
        suppressed = conn.execute("SELECT COUNT(*) AS n FROM client_suppression WHERE client=?",
                                  (name,)).fetchone()["n"]
        out: Dict[str, Any] = {
            "client": name,
            "deliveries": int(row["runs"] or 0),
            "first_delivery": row["first"],
            "last_delivery": row["last"],
            "items": sum(by_kind.values()),
            "by_kind": dict(by_kind),
            "suppressed": int(suppressed or 0),
        }
        out.update(by_kind)
        return out

    def list_clients_with_history(self) -> List[str]:
        """Clients that have received at least one delivery, sorted."""
        return [r["client"] for r in self.conn.execute(
            "SELECT DISTINCT client FROM deliveries ORDER BY client")]

    # --- client suppression -------------------------------------------------------------
    def suppress(self, client: Any, value: str, kind: Optional[str] = None, reason: str = "") -> str:
        """Put a value on the client's do-not-list; returns the stored (normalised) value.

        ``kind``: email, domain, company or linkedin (None = guessed from the value).
        Raises ``ValueError`` with a plain-English message for a bad kind / value.
        """
        name = _client_name(client)
        k = str(kind).strip().lower() if kind else guess_kind(value)
        if k not in SUPPRESSION_KINDS:
            raise ValueError(f"kind must be one of {', '.join(SUPPRESSION_KINDS)} (got {kind!r})")
        v = normalize_suppression(str(value or ""), k)
        if not v:
            raise ValueError(f"nothing to suppress: the {k} value is empty")
        if k == "email" and "@" not in v:
            raise ValueError(f"{value!r} is not an email address - use kind 'domain' or 'company'")
        if k == "domain" and not _DOMAIN_RE.match(v):
            raise ValueError(f"{value!r} doesn't look like a website domain (e.g. acme.com) - "
                             f"use kind 'company' for a company name")
        self.conn.execute(
            "INSERT OR REPLACE INTO client_suppression (client, kind, value, reason, added_at) "
            "VALUES (?,?,?,?,?)", (name, k, v, str(reason or ""), _now()))
        self.conn.commit()
        return v

    def unsuppress(self, client: Any, value: str, kind: Optional[str] = None) -> int:
        """Remove a value from the client's do-not-list; returns how many entries went.
        Without ``kind`` every kind the value could be is removed."""
        name = _client_name(client)
        kinds: Sequence[str] = [str(kind).strip().lower()] if kind else SUPPRESSION_KINDS
        removed = 0
        for k in kinds:
            if k not in SUPPRESSION_KINDS:
                raise ValueError(f"kind must be one of {', '.join(SUPPRESSION_KINDS)} (got {kind!r})")
            v = normalize_suppression(str(value or ""), k)
            if v:
                cur = self.conn.execute("DELETE FROM client_suppression WHERE client=? AND kind=? AND value=?",
                                        (name, k, v))
                removed += cur.rowcount
        self.conn.commit()
        return removed

    def is_suppressed(self, client: Any, email: str = "", domain: str = "", company: str = "",
                      linkedin: str = "") -> bool:
        """True if any given value is on the client's do-not-list. An email is also
        blocked by its domain; a domain also by its parent domains."""
        return self.suppression_match(client, email=email, domain=domain, company=company,
                                      linkedin=linkedin) is not None

    def suppression_match(self, client: Any, email: str = "", domain: str = "", company: str = "",
                          linkedin: str = "") -> Optional[str]:
        """The kind of the first matching do-not-list entry (``"email"``, ...), or None."""
        name = _client_name(client)
        checks: List[Tuple[str, str]] = []
        if email:
            checks.append(("email", normalize_suppression(email, "email")))
            checks += [("domain", d) for d in domain_and_parents(email)]
        if domain:
            checks += [("domain", d) for d in domain_and_parents(domain)]
        if company:
            checks.append(("company", normalize_suppression(company, "company")))
        if linkedin:
            checks.append(("linkedin", normalize_suppression(linkedin, "linkedin")))
        conn = self.conn
        for k, v in checks:
            if v and conn.execute("SELECT 1 FROM client_suppression WHERE client=? AND kind=? AND value=?",
                                  (name, k, v)).fetchone():
                return k
        return None

    def list_suppressed(self, client: Any = None) -> List[Dict[str, Any]]:
        """The client's do-not-list (every client's when ``client`` is None), newest first."""
        q, args = "SELECT client, kind, value, reason, added_at FROM client_suppression", []
        if client is not None:
            q += " WHERE client=?"
            args.append(_client_name(client))
        q += " ORDER BY added_at DESC, client, kind, value"
        return [dict(r) for r in self.conn.execute(q, args)]


# --- pipeline hooks -------------------------------------------------------------------------

class LedgerHooks(PipelineHooks):
    """Drops what a client already received, or must never receive, from a run.

    ``filter_company`` (right after the ICP filter, before any paid lookup):
      * the company is on the client's do-not-list (stored domain / company
        name, or the client file's ``exclusions``) -> dropped;
      * ``company`` in ``client.dedupe`` and the company was delivered -> dropped;
      * ``job`` in ``client.dedupe``: jobs (signals) already delivered are
        removed from ``company.signals``; a company left with no signal, or
        with no job posting when it had some -> "all its jobs were already
        delivered".
    ``filter_contact`` (before verification and again once the email is known):
      * the person is on the client's do-not-list (email / its domain /
        LinkedIn) -> skipped;
      * ``contact`` in ``client.dedupe`` and any identity of the person (email,
        LinkedIn, name at this company) was delivered -> skipped.

    The redelivery window is ``client.redelivery_days`` (None = never again).
    ``removed`` counts what was taken out: ``{"company": companies,
    "job": jobs (signals), "contact": people, "suppressed": companies + people}``.
    """

    def __init__(self, ledger: Ledger, client: Any, today: Optional[date] = None) -> None:
        self.ledger = ledger
        self.client = client
        self.client_name = _client_name(client)
        self.today = today or date.today()
        window = getattr(client, "redelivery_days", None)
        self.window: Optional[int] = int(window) if window is not None else None
        self.dedupe: Set[str] = set(getattr(client, "dedupe", DELIVERY_KINDS) or [])
        exclusions = getattr(client, "exclusions", None)
        companies = [str(x) for x in (getattr(exclusions, "companies", None) or [])]
        domains = [str(x) for x in (getattr(exclusions, "domains", None) or [])]
        self._excluded_names = {normalize_company_name(x) for x in companies} - {""}
        # a "company" written as a domain (bigclient.com) is treated as that domain too
        self._excluded_domains = {normalize_domain(x) for x in domains + companies
                                  if " " not in x.strip() and _DOMAIN_RE.match(normalize_domain(x) or "-")}
        self.removed: Dict[str, int] = {"company": 0, "job": 0, "contact": 0, "suppressed": 0}

    # --- helpers ----------------------------------------------------------------------
    def _domain_excluded(self, domain: str) -> bool:
        return bool(self._excluded_domains) and any(
            d in self._excluded_domains for d in domain_and_parents(domain))

    def _delivered(self, kind: str, key: str) -> Optional[date]:
        return self.ledger.delivered_on(self.client_name, kind, key, self.window, self.today)

    def _company_delivered(self, company: Company) -> Optional[date]:
        keys = [company_item_key(company)]
        if company.domain:  # delivered earlier while its domain was still unknown
            name = normalize_company_name(company.name)
            if name:
                keys.append(f"name:{name}")
        for key in keys:
            when = self._delivered("company", key)
            if when:
                return when
        return None

    # --- hooks -------------------------------------------------------------------------
    def filter_company(self, company: Company) -> Optional[str]:
        name = self.client_name
        if company.domain and (self.ledger.is_suppressed(name, domain=company.domain)
                               or self._domain_excluded(company.domain)):
            self.removed["suppressed"] += 1
            return "on this client's do-not-list (domain)"
        if (self.ledger.is_suppressed(name, company=company.name)
                or normalize_company_name(company.name) in self._excluded_names):
            self.removed["suppressed"] += 1
            return "on this client's do-not-list (company name)"
        if "company" in self.dedupe:
            when = self._company_delivered(company)
            if when:
                self.removed["company"] += 1
                return f"already delivered to this client ({when.isoformat()})"
        if "job" in self.dedupe and company.signals:
            had_jobs = any(s.type == SignalType.JOB_POSTING for s in company.signals)
            kept = [s for s in company.signals if not self._delivered("job", job_item_key(company, s))]
            dropped = len(company.signals) - len(kept)
            if dropped:
                self.removed["job"] += dropped
                company.signals[:] = kept
                if not kept or (had_jobs and not any(s.type == SignalType.JOB_POSTING for s in kept)):
                    return "all its jobs were already delivered"
        return None

    def filter_contact(self, company: Company, contact: Contact) -> Optional[str]:
        email = str(contact.email or "").strip()
        if (self.ledger.is_suppressed(self.client_name, email=email, linkedin=contact.linkedin_url)
                or (email and self._domain_excluded(email))):
            self.removed["suppressed"] += 1
            return "on this client's do-not-list (contact)"
        if "contact" in self.dedupe:
            for key in contact_item_keys(contact, company):
                if self._delivered("contact", key):
                    self.removed["contact"] += 1
                    return "contact already delivered to this client"
        return None
