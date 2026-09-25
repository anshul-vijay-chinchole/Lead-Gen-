"""``deliver()``: one client's weekly Hiring Signal Report, from the sources to the files.

What happens, in order (``leadgen deliver --client NAME`` calls this):

1. **Settings.** ``client_playbook(client)`` builds the playbook (the base
   playbook + the client's targeting) and it always runs in ``mode: delivery``:
   no email copy is written, nothing is handed to a sending tool, replies and
   follow-ups are never touched. The run's ``Context`` gets a ``UsageMeter``
   whose paid-lookup cap is the ``budget`` argument (``--budget N``), else the
   client file's ``budget.max_paid_lookups``, else the base playbook's
   ``usage.max_paid_lookups``.
2. **Pipeline.** ``Pipeline(ctx, out_dir=<folder>/_internal, hooks=LedgerHooks(...))``
   finds companies, keeps fresh matching jobs, applies the client's criteria,
   removes what the client already received / must never receive (before any
   paid lookup), finds + verifies the decision-maker and scores every lead.
3. **Selection** (``select_leads``): only leads whose urgency tier is in the
   client's ``tiers`` and that have a live job posting (or another *primary*
   signal of the playbook); one row per company (its best lead) and per person
   (no two rows with the same email / LinkedIn profile); hot leads first, then
   by score (then the freshest job); cut to ``leads_per_week``. Leads over the
   limit are "held back": not recorded, so they can go in a later delivery.
4. **Rows.** ``build_row`` with the client's email policy (``emails.include_unverified``
   false: only "verified" emails; a pattern-guessed address is never "verified").
   The "Suggested opening line" column is added when ``opening_line.enabled``.
5. **Files.** ``write_all`` (CSV / Excel / HTML, ``delivery.formats``) and, when
   ``delivery.google_sheet.spreadsheet_id`` is set, a Google Sheets push (never
   in a dry run; a failed push is a QA warning - the files are still complete).
6. **Ledger.** The company, job and contact keys of every delivered row are
   recorded, so they are never delivered to this client again (see
   ``redelivery_days`` / ``dedupe``). Not in a dry run: dry-run files are named
   ``...-PREVIEW.<ext>`` and nothing is recorded.
7. **QA.** ``build_qa`` -> ``<folder>/_internal/qa.txt`` + ``qa.json``; returned
   in ``DeliveryResult.qa`` (the CLI prints ``qa.lines()``).

Output folder: ``out_dir`` when given (``{client}`` / ``{date}`` placeholders
are filled in), else the client file's ``delivery.folder`` (default
``deliveries/{client}/{date}``). A folder that already holds this client's
delivery files for the same date is never overwritten: the new files go to
``<folder>-2`` (``-3`` ...) and the QA report says so. Layout::

    <folder>/
        <client>-hiring-signals-<YYYY-MM-DD>.csv / .xlsx / .html   (the client's files)
        _internal/                                                 (yours, not the client's)
            <run id>/rejected.csv, summary.json   the pipeline run (+ review exporter files)
            not_delivered.csv                     every company / lead left out, and why
            qa.txt, qa.json                       the QA summary

Client settings read (``delivery.client.Client``): ``name``, ``display_name``,
``leads_per_week``, ``tiers``, ``freshness_days``, ``allow_undated``,
``drop_reposts``, ``dedupe``, ``redelivery_days`` (the last five also shape the
file's notes), ``emails.include_unverified``, ``opening_line.enabled`` /
``.ai`` / ``.max_cost_usd``, ``delivery.formats`` / ``.folder`` /
``.google_sheet``, plus everything ``client_playbook`` and ``LedgerHooks`` read.
Playbook keys read here: ``delivery.*`` (branding, passed to the formats as
``pkg.brand``), ``signals.primary``, ``usage.*`` (through ``UsageMeter``) and
``storage.path`` (only when no ``store`` is given).
"""
from __future__ import annotations

import csv
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union

from ..context import Context
from ..models import Contact, Lead, SignalType, Tier
from ..modes import DELIVERY
from ..pipeline import Pipeline, RunResult
from ..signals import primary_types
from ..store import Store, normalize_suppression
from ..usage import UsageMeter
from .client import Client, ClientError, client_playbook, load_client
from .formats import FORMATS, file_name, normalize_formats, push_google_sheet, write_all
from .ledger import Ledger, LedgerHooks, lead_items
from .opening import OpeningStats, add_opening_lines
from .qa import KIND_DUPLICATE, KIND_FILTERED, KIND_OVER_LIMIT, QAReport, build_qa
from .rows import DeliveryPackage, build_row

INTERNAL_DIR = "_internal"
TIER_RANK: Dict[str, int] = {Tier.HOT: 0, Tier.NORMAL: 1, Tier.SKIP: 2}
MAX_FOLDER_SUFFIX = 99


# --- results -------------------------------------------------------------------------------

@dataclass
class DeliveryResult:
    """Everything one ``deliver()`` call produced."""

    client: Client
    files: Dict[str, Path]              # format -> file written
    sheet_url: str                      # "" when not pushed (not configured, dry run or failed)
    package: DeliveryPackage            # the rows + branding the files were made from
    qa: QAReport
    run: RunResult                      # the pipeline run (counts, rejected, errors, leads)
    dry_run: bool
    folder: Optional[Path] = None       # where the client's files are
    internal_dir: Optional[Path] = None  # <folder>/_internal
    recorded: int = 0                   # ledger items recorded (0 in a dry run)
    not_delivered: List[Dict[str, str]] = field(default_factory=list)   # selection drops
    opening: Optional[OpeningStats] = None

    @property
    def delivered(self) -> int:
        return len(self.package.rows)

    def lines(self) -> List[str]:
        """The QA summary lines (what the CLI prints)."""
        return self.qa.lines()

    def summary(self) -> str:
        return "\n".join(self.lines())


@dataclass
class Selection:
    """The outcome of ``select_leads``: the leads to deliver (best first) and the rest."""

    leads: List[Lead] = field(default_factory=list)
    dropped: List[Dict[str, str]] = field(default_factory=list)   # {company, domain, contact, stage, kind, reason}

    def count(self, kind: str) -> int:
        return sum(1 for d in self.dropped if d.get("kind") == kind)

    @property
    def duplicates(self) -> Dict[str, int]:
        """Leads dropped because their company / person was already in this delivery."""
        out = {"company": 0, "contact": 0}
        for d in self.dropped:
            if d.get("kind") == KIND_DUPLICATE:
                of = d.get("duplicate_of") or "company"
                out[of] = out.get(of, 0) + 1
        return out

    @property
    def held_back(self) -> int:
        return self.count(KIND_OVER_LIMIT)


# --- selection -------------------------------------------------------------------------------

def _norm_type(value: Any) -> str:
    return str(value or "").strip().lower()


def job_signals(lead: Lead, primary: Iterable[str] = (SignalType.JOB_POSTING,)) -> List[Any]:
    """The lead's live job postings (and other signals of a primary type)."""
    wanted = {_norm_type(t) for t in primary} | {SignalType.JOB_POSTING}
    return [s for s in (lead.company.signals or []) if _norm_type(getattr(s, "type", "")) in wanted]


def person_keys(contact: Optional[Contact]) -> Set[str]:
    """Identities of a person for "one row per person": email and LinkedIn profile."""
    if contact is None:
        return set()
    keys: Set[str] = set()
    email = str(contact.email or "").strip().lower()
    if email:
        keys.add(f"email:{email}")
    if contact.linkedin_url:
        li = normalize_suppression(contact.linkedin_url, "linkedin")
        if li:
            keys.add(f"linkedin:{li}")
    return keys


def rank_key(lead: Lead, today: date, primary: Iterable[str] = (SignalType.JOB_POSTING,)) -> Tuple[Any, ...]:
    """Sort key: hot first, then higher score, then the freshest job, then name (stable)."""
    ages = [a for a in (s.age_days(today) for s in job_signals(lead, primary)) if a is not None]
    freshest = min(ages) if ages else 10 ** 6
    return (TIER_RANK.get(str(lead.tier), len(TIER_RANK)), -int(lead.score or 0), freshest,
            (lead.company.name or "").lower(), lead.id)


def _drop(lead: Lead, kind: str, reason: str, **extra: str) -> Dict[str, str]:
    entry = {"company": lead.company.name, "domain": lead.company.domain,
             "contact": lead.contact.full_name if lead.contact else "",
             "stage": "selection", "kind": kind, "reason": reason}
    entry.update(extra)
    return entry


def select_leads(leads: Sequence[Lead], client: Client, primary: Iterable[str] = (SignalType.JOB_POSTING,),
                 today: Optional[date] = None) -> Selection:
    """Pick what goes in the file (see step 3 in the module docstring).

    ``primary`` = the playbook's primary signal types (a job posting always counts).
    """
    today = today or date.today()
    primary = list(primary)
    tiers = [str(t).strip().lower() for t in (client.tiers or [])]
    limit = max(0, int(client.leads_per_week or 0))
    sel = Selection()
    candidates: List[Lead] = []
    for lead in leads:
        tier = str(lead.tier or "").lower()
        if not job_signals(lead, primary):
            sel.dropped.append(_drop(lead, KIND_FILTERED,
                                     "no live job posting (only other signals, e.g. funding news)"))
        elif tier not in tiers:
            sel.dropped.append(_drop(lead, KIND_FILTERED,
                                     f"urgency '{tier or 'none'}' is not delivered to this client "
                                     f"(tiers: {', '.join(tiers) or 'none'})"))
        else:
            candidates.append(lead)

    seen_companies: Set[str] = set()
    seen_people: Set[str] = set()
    for lead in sorted(candidates, key=lambda ld: rank_key(ld, today, primary)):
        people = person_keys(lead.contact)
        if lead.company.key in seen_companies:
            sel.dropped.append(_drop(lead, KIND_DUPLICATE, "same company as a higher-ranked lead in this delivery",
                                     duplicate_of="company"))
            continue
        if people & seen_people:
            sel.dropped.append(_drop(lead, KIND_DUPLICATE, "same person as a higher-ranked lead in this delivery",
                                     duplicate_of="contact"))
            continue
        if len(sel.leads) >= limit:
            sel.dropped.append(_drop(lead, KIND_OVER_LIMIT,
                                     f"over this client's weekly limit of {limit} leads (not recorded - it can "
                                     f"go in a later delivery)"))
            continue
        sel.leads.append(lead)
        seen_companies.add(lead.company.key)
        seen_people |= people
    return sel


# --- folders + notes -----------------------------------------------------------------------------

def delivery_folder(client: Client, today: date, out_dir: Union[str, Path, None] = None) -> Path:
    """Where this delivery's files go: ``out_dir`` if given, else ``delivery.folder``
    (``{client}`` / ``{date}`` filled in)."""
    if out_dir is None or str(out_dir).strip() == "":
        return client.delivery_folder(today)
    raw = os.path.expanduser(str(out_dir))
    try:
        raw = raw.format(client=client.name, date=today.isoformat())
    except (KeyError, IndexError, ValueError):
        pass  # braces that are not our placeholders: use the path as typed
    return Path(raw)


def _delivery_file_names(client: Client, today: date) -> List[str]:
    probe = DeliveryPackage(client_name=client.name, client_display=client.display_name,
                            period_start=today, period_end=today, rows=[])
    return [file_name(probe, ext) for ext in FORMATS]


def free_folder(folder: Path, client: Client, today: date) -> Path:
    """``folder``, or ``folder-2`` / ``-3`` ... when it already holds this client's
    (real, not preview) delivery files for ``today`` - a delivery is never overwritten."""
    names = _delivery_file_names(client, today)
    base = folder if folder.name else folder.resolve()
    candidate, n = folder, 1
    while any((candidate / name).exists() for name in names):
        n += 1
        if n > MAX_FOLDER_SUFFIX:
            raise ClientError(f"{client.where}: {base} and {MAX_FOLDER_SUFFIX - 1} numbered copies already hold "
                              f"deliveries for {today.isoformat()} - tidy them up or pass another output folder")
        candidate = base.with_name(f"{base.name}-{n}")
    return candidate


def package_notes(client: Client, today: date, dry_run: bool = False) -> List[str]:
    """Short factual notes printed in the files (Excel "About" sheet, HTML page)."""
    notes = [f"Jobs posted in the last {client.freshness_days} days (as of {today.isoformat()})."]
    if not client.allow_undated:
        notes.append("Jobs without a posting date are left out.")
    if client.drop_reposts:
        notes.append("Re-posted (stale) job ads are left out.")
    plural = {"company": "companies", "job": "jobs", "contact": "contacts"}
    items = [plural[k] for k in ("company", "job", "contact") if k in set(client.dedupe or [])]
    if items:
        what = items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
        when = ("in earlier deliveries" if client.redelivery_days is None
                else f"in the last {client.redelivery_days} days")
        notes.append(f"{what[:1].upper() + what[1:]} already sent to you {when} are left out.")
    if not client.email_policy_include_unverified:
        notes.append("Only verified emails are included; other addresses show as 'not found'.")
    if dry_run:
        notes.append("PREVIEW - not recorded as delivered.")
    return notes


def _check_formats(client: Client) -> List[str]:
    try:
        formats = normalize_formats(client.delivery.formats)
    except ValueError as e:
        raise ClientError(f"{client.where}: delivery.formats: {e}") from None
    if not formats:
        raise ClientError(f"{client.where}: delivery.formats is empty - list at least one of: "
                          f"{', '.join(FORMATS)}")
    return formats


def _check_budget(budget: Any) -> Optional[int]:
    if budget is None:
        return None
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
        raise ValueError(f"budget must be a whole number >= 0 (0 = no cap), got {budget!r}")
    return budget


# --- google sheets ---------------------------------------------------------------------------------

def _push_sheet(pkg: DeliveryPackage, client: Client, ctx: Context, dry_run: bool,
                warnings: List[str], notes: List[str]) -> str:
    cfg = dict(client.delivery.google_sheet or {})
    if not str(cfg.get("spreadsheet_id") or cfg.get("spreadsheet_url") or "").strip():
        return ""
    if dry_run:
        notes.append("Google Sheet not updated (dry run)")
        return ""
    try:
        return push_google_sheet(pkg, cfg, ctx)
    except Exception as e:  # noqa: BLE001 - the files are written; a sheet problem must not lose them
        ctx.log.warning("google sheet push failed: %s", e)
        warnings.append(f"Google Sheet not updated: {e}. The files in the delivery folder are complete - "
                        f"send those, and fix delivery.google_sheet in the client file for next time.")
        return ""


# --- internal files --------------------------------------------------------------------------------

def _write_internal(internal: Path, qa: QAReport, run: RunResult, sel: Selection) -> None:
    internal.mkdir(parents=True, exist_ok=True)
    (internal / "qa.txt").write_text(qa.text() + "\n", encoding="utf-8")
    (internal / "qa.json").write_text(json.dumps(qa.to_dict(), indent=2, ensure_ascii=False) + "\n",
                                      encoding="utf-8")
    fields = ["company", "domain", "contact", "stage", "reason"]
    with open(internal / "not_delivered.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in run.rejected or []:
            w.writerow({k: r.get(k, "") for k in fields})
        w.writerows(sel.dropped)


# --- deliver -----------------------------------------------------------------------------------------

def deliver(client: Union[Client, str], *, store: Any, env: Optional[Dict[str, str]] = None, http: Any = None,
            today: Optional[date] = None, dry_run: bool = False, budget: Optional[int] = None,
            out_dir: Union[str, Path, None] = None, log: Optional[logging.Logger] = None) -> DeliveryResult:
    """Run one delivery for ``client`` and return what it produced (see the module docstring).

    ``client``   a ``Client`` (``load_client``) or a client name / file path.
    ``store``    the ``Store`` holding the ledger (None = open the playbook's ``storage.path``).
    ``env``      environment for ``${VAR}`` expansion and API keys (default: ``os.environ``).
    ``http``     HTTP client (default: a real ``HttpClient``; tests pass a fake).
    ``today``    the delivery date (default: the system date).
    ``dry_run``  preview: offline adapters only, files named ``...-PREVIEW``, nothing recorded,
                 no Google Sheets push.
    ``budget``   cap on paid lookups for this run (overrides the client file / playbook).
    ``out_dir``  output folder (overrides the client file's ``delivery.folder``).
    ``log``      logger (default ``logging.getLogger("leadgen")``).

    Raises ``ClientError`` for client / playbook problems (before anything is looked up)
    and ``ValueError`` for a bad ``budget``.
    """
    if not isinstance(client, Client):
        client = load_client(str(client))
    today = today or date.today()
    log = log or logging.getLogger("leadgen")
    budget = _check_budget(budget)
    formats = _check_formats(client)

    pb = client_playbook(client, env=env)
    pb.mode = DELIVERY  # client_playbook already forces it; a delivery never writes, sends or replies
    wanted_folder = delivery_folder(client, today, out_dir)
    folder = free_folder(wanted_folder, client, today)
    internal = folder / INTERNAL_DIR

    own_store = None
    if store is None:
        store = own_store = Store(pb.db_path)
    try:
        ctx = Context(playbook=pb, http=http, store=store,
                      env=dict(env) if env is not None else dict(os.environ),
                      today=today, log=log, dry_run=bool(dry_run),
                      usage=UsageMeter.from_playbook(pb, budget))
        ledger = Ledger(store)
        hooks = LedgerHooks(ledger, client, today)
        log.info("delivery %s: running the pipeline (%s)%s", client.name, pb.name, " - dry run" if dry_run else "")
        run = Pipeline(ctx, out_dir=internal, hooks=hooks).run()

        primary = sorted(primary_types(ctx) | {SignalType.JOB_POSTING})
        sel = select_leads(run.leads, client, primary=primary, today=today)

        include_opening = bool(client.opening_line.enabled)
        rows = [build_row(ld, today, include_unverified=client.email_policy_include_unverified,
                          include_opening=include_opening) for ld in sel.leads]
        opening: Optional[OpeningStats] = None
        if include_opening:
            opening = add_opening_lines(rows, {r["_lead_id"]: ld for r, ld in zip(rows, sel.leads)}, ctx, client)

        warnings: List[str] = []
        notes: List[str] = []
        if folder != wanted_folder:
            notes.append(f"{wanted_folder} already holds a delivery for {today.isoformat()} - these files went "
                         f"to {folder} so nothing was overwritten")
        pkg = DeliveryPackage(
            client_name=client.name, client_display=client.display_name,
            period_start=today - timedelta(days=int(client.freshness_days)), period_end=today,
            rows=rows, include_opening=include_opening, brand=dict(pb.delivery or {}),
            run_id=run.run_id, notes=package_notes(client, today, dry_run))
        files = write_all(pkg, folder, formats, preview=bool(dry_run))
        sheet_url = _push_sheet(pkg, client, ctx, bool(dry_run), warnings, notes)

        recorded = 0
        if not dry_run and sel.leads:
            items: List[Tuple[str, str]] = []
            for ld in sel.leads:
                items += lead_items(ld)
            recorded = ledger.record(client.name, items, run.run_id, today)
            log.info("delivery %s: %d item(s) recorded in the ledger", client.name, recorded)

        qa = build_qa(client, run, pkg, hooks.removed, sel.duplicates, opening, ctx.usage,
                      not_delivered=sel.dropped, dry_run=bool(dry_run), extra_warnings=warnings, notes=notes)
        qa.folder = str(folder)
        qa.files = {fmt: str(path) for fmt, path in files.items()}
        qa.sheet_url = sheet_url
        _write_internal(internal, qa, run, sel)
        log.info("delivery %s: %d lead(s) written to %s", client.name, len(rows), folder)
        return DeliveryResult(client=client, files=files, sheet_url=sheet_url, package=pkg, qa=qa, run=run,
                              dry_run=bool(dry_run), folder=folder, internal_dir=internal, recorded=recorded,
                              not_delivered=sel.dropped, opening=opening)
    finally:
        if own_store is not None:
            own_store.close()


__all__ = ["DeliveryResult", "INTERNAL_DIR", "Selection", "deliver", "delivery_folder", "free_folder",
           "job_signals", "package_notes", "person_keys", "rank_key", "select_leads"]
