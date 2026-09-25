"""The factory line: SOURCE -> SIGNALS -> ICP FILTER -> ENRICH -> VERIFY -> SCORE -> WRITE -> EXPORT.

``Pipeline(ctx).run()`` executes one full run for the playbook in ``ctx`` and
returns a ``RunResult``. Every stage is defensive: one broken source or a
missing API key logs an error and the run carries on with what it has.

Modes (``playbook.mode``):
  * ``delivery`` (default) - the run stops after SCORE + review export: no email
    copy is written (WRITE is skipped) and hand-over exporters (Instantly,
    Smartlead, upload CSVs, webhooks) are never built. Lead files only.
  * ``outbound`` - the full outreach line, WRITE + hand-over included.

``PipelineHooks`` let a caller (the client delivery layer) drop companies /
jobs / contacts that must not appear - e.g. already delivered to this client -
right after the ICP filter, before any paid enrichment is spent on them.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import registry
from .context import Context, MissingCredentialError
from .modes import is_outbound
from .contacts import ContactWaterfall, select_contacts
from .filters import apply_icp, excluded_domain
from .http import HttpError, redact
from .models import Company, Contact, EmailStatus, Lead, Stage, Tier
from .notify import notify
from .outbound.base import ExportResult
from .scoring import score, tier_for
from .signals import process_signals
from .usage import BudgetExceeded
from .utils import is_personal_email, normalize_company_name, normalize_domain
from .writer import build_writer

MAX_CANDIDATES_TO_VERIFY = 3


@dataclass
class RunResult:
    run_id: str
    playbook: str
    out_dir: Path
    counts: Dict[str, int] = field(default_factory=dict)
    leads: List[Lead] = field(default_factory=list)
    rejected: List[Dict[str, str]] = field(default_factory=list)
    exports: List[ExportResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    mode: str = "delivery"
    usage: List[str] = field(default_factory=list)   # UsageMeter.summary_lines()

    def summary(self) -> str:
        c = self.counts
        if self.mode != "outbound":
            lines = [
                f"Run {self.run_id} ({self.playbook}, delivery mode: lead files only - nothing is written or sent)",
                f"  sourced companies ....... {c.get('sourced', 0)}",
                f"  with a live signal ...... {c.get('with_signal', 0)}",
                f"  match the ICP ........... {c.get('qualified', 0)}",
            ]
            if c.get("hook_rejected"):
                lines.append(f"  removed (already delivered / suppressed) ... {c.get('hook_rejected', 0)}")
            lines += [
                f"  decision-maker found .... {c.get('enriched', 0)}",
                f"  email verified .......... {c.get('verified', 0)}",
                f"  hot / normal / skip ..... {c.get('hot', 0)} / {c.get('normal', 0)} / {c.get('skip', 0)}",
                f"  output .................. {self.out_dir}",
            ]
            for e in self.exports:
                lines.append(f"    - {e.exporter}: {e.count} {e.path or e.detail}".rstrip())
            lines += [f"  {u}" for u in self.usage]
            lines += [f"  warning: {w}" for w in self.warnings]
            if self.errors:
                lines.append(f"  errors ({len(self.errors)}):")
                lines += [f"    ! {e}" for e in self.errors]
            return "\n".join(lines)
        lines = [
            f"Run {self.run_id} ({self.playbook})",
            f"  sourced companies ....... {c.get('sourced', 0)}",
            f"  with a live signal ...... {c.get('with_signal', 0)}",
            f"  match the ICP ........... {c.get('qualified', 0)}",
            f"  decision-maker found .... {c.get('enriched', 0)}",
            f"  email verified .......... {c.get('verified', 0)}",
            f"  email accepted .......... {c.get('usable_email', 0)}",
            f"  hot / normal / skip ..... {c.get('hot', 0)} / {c.get('normal', 0)} / {c.get('skip', 0)}",
            f"  sequences written ....... {c.get('written', 0)}",
            f"  handed to outbound ...... {c.get('exported', 0)}",
            f"  output .................. {self.out_dir}",
        ]
        for e in self.exports:
            lines.append(f"    - {e.exporter}: {e.count} {e.path or e.detail}".rstrip())
        lines += [f"  {u}" for u in self.usage]
        lines += [f"  warning: {w}" for w in self.warnings]
        if self.errors:
            lines.append(f"  errors ({len(self.errors)}):")
            lines += [f"    ! {e}" for e in self.errors]
        return "\n".join(lines)


class PipelineHooks:
    """Optional per-run filters. Subclass and override; the defaults keep everything.

    ``filter_company`` runs right after the ICP filter (before enrichment, so no
    paid lookups are spent on companies that will be dropped). It may remove
    signals from ``company.signals`` (e.g. jobs already delivered); return a
    reason to drop the whole company. ``filter_contact`` runs before a contact
    is verified and again once its email is known; return a reason to skip it.
    """

    def filter_company(self, company: Company) -> Optional[str]:
        return None

    def filter_contact(self, company: Company, contact: Contact) -> Optional[str]:
        return None

    def filter_enriched(self, company: Company) -> Optional[str]:
        """Runs after enrichment, before any email is verified: more is known now (e.g.
        the email domain of a company that arrived without a website). Return a
        reason to drop the company."""
        return None


def _enriched_domains(company: Company) -> List[str]:
    """Domains learnt about a company without a website: provider hint + people's emails."""
    if company.domain:
        return []
    data = company.data if isinstance(company.data, dict) else {}
    found = [str(data.get("email_domain") or "")]
    found += [normalize_domain(ct.email) for ct in company.contacts or []
              if isinstance(ct, Contact) and ct.email and not is_personal_email(ct.email)]
    return [d for d in dict.fromkeys(found) if d]


def merge_companies(companies: List[Company]) -> List[Company]:
    """Dedupe companies across sources by domain, falling back to normalized name."""
    by_domain: Dict[str, Company] = {}
    by_name: Dict[str, Company] = {}
    out: List[Company] = []
    for c in companies:
        nname = normalize_company_name(c.name)
        target = (by_domain.get(c.domain) if c.domain else None) or (by_name.get(nname) if nname else None)
        if target is not None and target.domain and c.domain and target.domain != c.domain:
            target = None  # same name, different domains: different companies
        if target is None:
            out.append(c)
            target = c
        else:
            target.merge(c)
        if target.domain:
            by_domain.setdefault(target.domain, target)
        if nname:
            by_name.setdefault(nname, target)
    return out


class Pipeline:
    def __init__(self, ctx: Context, out_dir: Optional[Path] = None, limit: Optional[int] = None,
                 hooks: Optional[PipelineHooks] = None):
        self.ctx = ctx
        self.pb = ctx.playbook
        self.limit = limit
        self.hooks = hooks
        self.base_out = Path(out_dir) if out_dir else Path("output") / self.pb.name
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.rejected: List[Dict[str, str]] = []
        self.outbound = is_outbound(ctx)

    def _warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)
            self.ctx.log.warning(msg)

    # --- helpers --------------------------------------------------------------
    def _err(self, where: str, e: Exception) -> None:
        msg = redact(f"{where}: {e}")
        self.errors.append(msg)
        self.ctx.log.error(msg)

    def _reject(self, company: Company, stage: str, reason: str) -> None:
        self.rejected.append({"company": company.name, "domain": company.domain,
                              "stage": stage, "reason": reason})

    def _make(self, kind: str, cfg: Dict[str, Any]) -> Any:
        adapter = registry.create(kind, cfg, self.ctx)
        if self.ctx.dry_run and not getattr(adapter, "offline", False):
            self.ctx.log.info("dry-run: skipping %s '%s' (uses the network)", kind, cfg.get("type"))
            return None
        return adapter

    # --- stages ---------------------------------------------------------------
    def collect(self) -> List[Company]:
        companies: List[Company] = []
        for cfg in self.pb.sources:
            if cfg.get("enabled") is False:
                continue
            label = cfg.get("label") or cfg.get("type")
            try:
                src = self._make("source", cfg)
                if src is None:
                    continue
                got = src.fetch()
                self.ctx.log.info("source %s: %d companies", label, len(got))
                companies.extend(got)
                stop = str(getattr(src, "budget_stop", "") or "")
                if stop:  # the source hit the budget part-way: what it had already paid for is kept
                    self._warn(f"source {label} stopped early: {stop} (kept the {len(got)} companies "
                               f"already found)")
            except BudgetExceeded as e:
                self._warn(f"source {label} stopped: {e}")
            except (MissingCredentialError, HttpError, registry.UnknownAdapterError, OSError, ValueError) as e:
                self._err(f"source {label}", e)
        return merge_companies(companies)

    def _verifier(self) -> Any:
        cfg = self.pb.enrichment.get("verifier")
        if not cfg:
            return None
        if isinstance(cfg, dict) and cfg.get("enabled") is False:  # switched off: never the paid one
            cfg = {"type": "basic"}
        try:
            v = self._make("verifier", cfg)
        except (MissingCredentialError, registry.UnknownAdapterError) as e:
            self._err("verifier", e)
            v = None
        if v is None:  # dry-run or broken: fall back to the offline syntax checker
            v = registry.create("verifier", {"type": "basic"}, self.ctx)
        return v

    def email_ok(self, contact: Optional[Contact]) -> bool:
        """Deliverable enough to hand over: status in ``enrichment.accept_statuses``;
        a *guessed* address (built from a name pattern) must also be in
        ``enrichment.accept_guessed_statuses`` (default: valid only)."""
        if not contact or not contact.email:
            return False
        enr = self.pb.enrichment
        if contact.email_status not in set(enr.get("accept_statuses") or []):
            return False
        if contact.data.get("email_guessed"):
            return contact.email_status in set(enr.get("accept_guessed_statuses") or [EmailStatus.VALID])
        return True

    @staticmethod
    def email_confirmed(contact: Optional[Contact]) -> bool:
        """A checker (or the data provider) said "valid" AND the address was not built from
        a name pattern - the rule behind the "verified" label in client files
        (``delivery.rows.email_label``). ``email_ok`` only says the address is acceptable."""
        from .delivery.rows import is_guessed
        return bool(contact and contact.email and contact.email_status == EmailStatus.VALID
                    and not is_guessed(contact))

    def verify_contact(self, contact: Contact, verifier: Any) -> None:
        """Set contact.email/email_status, trying guessed candidates if needed."""
        store = self.ctx.store
        accept = set(self.pb.enrichment.get("accept_statuses") or [])
        candidates = [contact.email] if contact.email else []
        candidates += [e for e in contact.email_candidates if e and e not in candidates]
        if not candidates or verifier is None:
            return
        best: Optional[Tuple[str, str]] = None
        meter = getattr(self.ctx, "usage", None)
        for email in candidates[: max(1, MAX_CANDIDATES_TO_VERIFY)]:
            status = store.get_verification(email, today=self.ctx.today) if store else None
            if status is None and meter is not None and meter.exhausted and getattr(verifier, "paid", False):
                self._warn("paid-lookup budget reached: remaining emails checked with the free basic checker")
                verifier = registry.create("verifier", {"type": "basic"}, self.ctx)
            if status is None:
                try:
                    res = verifier.verify(email)
                    status = res.status
                except Exception as e:  # noqa: BLE001 - provider down/bad key: keep going
                    self._err(f"verify {email}", e)
                    status = EmailStatus.UNKNOWN
                else:
                    if store and status != EmailStatus.UNKNOWN:
                        store.put_verification(email, status, getattr(verifier, "name", ""))
            if status == EmailStatus.VALID:
                best = (email, status)
                break
            if best is None or (status in accept and best[1] not in accept):
                best = (email, status)
            # a provider-supplied address that is catch-all/unknown is still our best bet
            if email == contact.email and status in accept:
                break
        if best:
            if best[0] != contact.email:
                contact.data["email_guessed"] = True
            contact.email, contact.email_status = best

    def run(self) -> RunResult:
        ctx, pb, store = self.ctx, self.pb, self.ctx.store
        run_id = store.start_run(pb.name, {"dry_run": ctx.dry_run, "limit": self.limit,
                                           "out_dir": str(self.base_out)})
        self.run_id = run_id
        out_dir = self.base_out / run_id
        out_dir.mkdir(parents=True, exist_ok=True)
        counts: Dict[str, int] = {}

        # 1. SOURCE
        companies = self.collect()
        counts["sourced"] = len(companies)

        # 2. SIGNALS (why now?)
        companies, rej = process_signals(companies, ctx)
        for c, reason in rej:
            self._reject(c, "signals", reason)
        counts["with_signal"] = len(companies)

        # 3. ICP FILTER (do they fit?)
        companies, rej = apply_icp(companies, ctx)
        for c, reason in rej:
            self._reject(c, "icp", reason)
        counts["qualified"] = len(companies)

        # 3b. HOOKS (e.g. already delivered to this client) - before any paid lookup
        if self.hooks is not None:
            kept: List[Company] = []
            for c in companies:
                reason = self.hooks.filter_company(c)
                if reason:
                    self._reject(c, "delivery", reason)
                else:
                    kept.append(c)
            counts["hook_rejected"] = len(companies) - len(kept)
            companies = kept

        # 4. PRE-SCORE + enrichment budget (spend credits on the best accounts first)
        prescored = sorted(((score(c, None, ctx).total, c) for c in companies),
                           key=lambda t: t[0], reverse=True)
        if self.limit:
            prescored = prescored[: self.limit]
        enr = pb.enrichment
        budget = int(enr.get("max_companies") or 0) or len(prescored)
        min_pre = enr.get("min_prescore") or 0

        # 5. ENRICH (who do we talk to?) + 6. VERIFY
        waterfall = ContactWaterfall(ctx, errors=self.errors)
        verifier = self._verifier()
        per_company = int(pb.buyers.get("max_contacts_per_company") or 1)
        leads: List[Lead] = []
        enriched_n = 0
        meter = getattr(ctx, "usage", None)
        for i, (pre, company) in enumerate(prescored):
            if i < budget and pre >= min_pre:
                if meter is not None and meter.exhausted:
                    self._warn(f"paid-lookup budget of {meter.max_paid_lookups} reached: "
                               f"no more contact lookups this run")
                try:
                    waterfall.enrich(company)
                except BudgetExceeded as e:
                    self._warn(f"enrichment stopped: {e}")
                except Exception as e:  # noqa: BLE001 - never lose the run to one company
                    self._err(f"enrich {company.name}", e)
            # a company that arrived without a website may turn out to be at a
            # suppressed / excluded domain once its people are known
            late = None
            for d in _enriched_domains(company):
                if store.is_suppressed(domain=d):
                    late = f"domain {d} is on the suppression list (found via its people)"
                elif excluded_domain(d, ctx):
                    late = f"excluded domain {d} (found via its people)"
                if late:
                    break
            if late:
                self._reject(company, "icp", late)
                continue
            if self.hooks is not None:
                late = self.hooks.filter_enriched(company)
                if late:
                    self._reject(company, "delivery", late)
                    counts["hook_rejected"] = counts.get("hook_rejected", 0) + 1
                    continue
            chosen = select_contacts(company, ctx, limit=per_company * 3)
            accepted: List[Contact] = []
            good = 0
            for contact in chosen:
                if good >= per_company:
                    break  # enough deliverable people: don't spend verification credits on the rest
                if store.is_suppressed(email=contact.email, linkedin=contact.linkedin_url):
                    counts["suppressed_contacts"] = counts.get("suppressed_contacts", 0) + 1
                    continue
                if self.hooks is not None and self.hooks.filter_contact(company, contact):
                    continue
                if not contact.email or contact.email_status == EmailStatus.UNKNOWN:
                    self.verify_contact(contact, verifier)
                    if contact.email and store.is_suppressed(email=contact.email):
                        counts["suppressed_contacts"] = counts.get("suppressed_contacts", 0) + 1
                        continue
                    if (contact.email and self.hooks is not None
                            and self.hooks.filter_contact(company, contact)):
                        continue
                accepted.append(contact)
                good += 1 if self.email_ok(contact) else 0
            # deliverable contacts first, keep ranking order otherwise
            accepted.sort(key=lambda ct: 0 if self.email_ok(ct) else 1)
            accepted = accepted[:per_company]
            if accepted:
                enriched_n += 1
            for contact in accepted or [None]:
                leads.append(Lead(company=company, contact=contact, playbook=pb.name, run_id=run_id,
                                  stage=Stage.QUALIFIED))
        counts["enriched"] = enriched_n

        # 7. SCORE
        accept = set(enr.get("accept_statuses") or [])
        dedupe_days = int(pb.outbound.get("dedupe_days") or 0)
        for lead in leads:
            bd = score(lead.company, lead.contact, ctx)
            lead.breakdown, lead.score = bd, bd.total
            lead.tier = tier_for(bd.total, ctx)
            ct = lead.contact
            if ct:
                lead.stage = Stage.ENRICHED
                if self.email_ok(ct) and (self.outbound or self.email_confirmed(ct)):
                    lead.stage = Stage.VERIFIED  # delivery mode: only a confirmed, non-guessed email
                elif ct.email and ct.data.get("email_guessed") and ct.email_status in accept:
                    lead.notes.append(f"guessed email only {ct.email_status} (needs valid)")
                elif ct.email:
                    lead.notes.append(f"email {ct.email_status}")
                else:
                    lead.notes.append("no email found")
                note = f"contacted in the last {dedupe_days} days"
                if self.outbound and ct.email and note not in lead.notes and store.recently_contacted(
                        ct.email, dedupe_days, ctx.today, exclude_lead_id=lead.id):
                    lead.notes.append(note)
            else:
                lead.notes.append("no decision-maker found")
        leads.sort(key=lambda ld: ld.score, reverse=True)
        counts["verified"] = sum(1 for ld in leads if self.email_confirmed(ld.contact))
        counts["usable_email"] = sum(1 for ld in leads if self.email_ok(ld.contact))
        for t in (Tier.HOT, Tier.NORMAL, Tier.SKIP):
            counts[t] = sum(1 for ld in leads if ld.tier == t)

        # 8. WRITE (outbound mode only: delivery mode never writes email copy)
        written = 0
        w_tiers = set(pb.writer.get("tiers") or [Tier.HOT, Tier.NORMAL])
        max_write = int(pb.writer.get("max_leads") or 0) or len(leads)
        writer = build_writer(ctx) if self.outbound else None
        written_emails: set = set()
        write_state: Dict[str, Dict[str, Any]] = {}
        for lead in (leads if self.outbound else []):
            if written >= max_write:
                break
            if lead.tier not in w_tiers or not lead.contact:
                continue
            if pb.outbound.get("require_email", True) and not self.email_ok(lead.contact):
                continue
            reason = self.block_reason(lead, write_state)
            if reason:  # would never be handed over: don't spend the writer (LLM) on it
                if reason not in lead.notes:
                    lead.notes.append(reason)
                continue
            em = (lead.contact.email or "").strip().lower()
            if em and em in written_emails:  # same person under two company records: keep the best-scored
                lead.notes.append("same email as a higher-scored lead in this run")
                continue
            if em:
                written_emails.add(em)
            try:
                out = writer.write(lead)
            except Exception as e:  # noqa: BLE001
                self._err(f"write {lead.company.name}", e)
                continue
            lead.messages = out.messages
            lead.personalization, lead.hypothesis, lead.writer = out.personalization, out.hypothesis, out.writer
            lead.notes.extend(out.warnings)
            if lead.messages:
                lead.stage = Stage.READY
                written += 1
        counts["written"] = written

        for lead in leads:
            store.save_lead(lead)

        # 9. EXPORT / SEND - hand-over exporters first, so review sheets written
        # afterwards show the final stage of every lead. Delivery mode: review
        # exporters only - a hand-over exporter is never even constructed.
        outbound = self.outbound_leads(leads) if self.outbound else []
        exports: List[ExportResult] = []
        handed: set = set()
        built = []
        for cfg in pb.outbound.get("exporters") or []:
            if not isinstance(cfg, dict) or cfg.get("enabled") is False:
                continue
            if not self.outbound and self._is_handover_type(str(cfg.get("type"))):
                self._warn(f"exporter '{cfg.get('type')}' hands leads to a sending tool - skipped "
                           f"(delivery mode; set 'mode: outbound' to use it)")
                continue
            try:
                exp = self._make("exporter", cfg)
            except Exception as e:  # noqa: BLE001
                self._err(f"exporter {cfg.get('type')}", e)
                continue
            if exp is not None:
                if not self.outbound and getattr(exp, "scope", "all") == "outbound":
                    self._warn(f"exporter '{cfg.get('type')}' skipped (delivery mode)")
                    continue
                built.append((cfg, exp))
        built.sort(key=lambda t: 0 if getattr(t[1], "scope", "all") == "outbound" else 1)
        for cfg, exp in built:
            is_outbound = getattr(exp, "scope", "all") == "outbound"
            if not is_outbound and handed:
                for lead in leads:
                    if lead.id in handed:
                        lead.stage = Stage.EXPORTED
            try:
                batch = outbound if is_outbound else leads
                res = exp.export(batch, out_dir)
                exports.append(res)
                if is_outbound and not ctx.dry_run:
                    ids = res.exported_ids or ([ld.id for ld in batch] if res.count == len(batch) else [])
                    for lid in ids:
                        if lid not in handed:
                            store.mark_exported(lid, res.exporter)
                            handed.add(lid)
            except Exception as e:  # noqa: BLE001
                self._err(f"exporter {cfg.get('type')}", e)
        for lead in leads:
            if lead.id in handed:
                lead.stage = Stage.EXPORTED
        counts["exported"] = len(handed)
        counts["outbound_eligible"] = len(outbound)
        if meter is not None:
            counts["paid_lookups"] = meter.paid_lookups

        usage_lines = meter.summary_lines() if meter is not None else []
        self._write_run_files(out_dir, leads, counts, usage=meter.rows() if meter is not None else [])
        store.finish_run(run_id, counts)
        result = RunResult(run_id=run_id, playbook=pb.name, out_dir=out_dir, counts=counts,
                           leads=leads, rejected=self.rejected, exports=exports, errors=self.errors,
                           warnings=self.warnings, mode=pb.mode, usage=usage_lines)
        notify(ctx, "run_summary", f"Run finished: {pb.name}", result.summary(),
               {"run_id": run_id, "counts": counts})
        return result

    @staticmethod
    def _is_handover_type(name: str) -> bool:
        """Exporter types that hand leads to a sending tool (checked without importing them)."""
        try:
            cls = registry.resolve("exporter", name)
        except Exception:  # noqa: BLE001 - unknown types are reported when built
            return False
        return getattr(cls, "scope", "all") == "outbound"

    def block_reason(self, lead: Lead, company_state: Dict[str, Dict[str, Any]]) -> Optional[str]:
        """Why this lead must NOT be handed over (None = it may be).

        Shared by the WRITE stage (so no copy - and no LLM spend - is produced for
        people who will never be emailed) and by ``outbound_leads``.
        """
        pb, store, ctx = self.pb, self.ctx.store, self.ctx
        ct = lead.contact
        if pb.outbound.get("require_email", True) and not self.email_ok(ct):
            return "no deliverable email"
        key = lead.company.key
        if key not in company_state:
            company_state[key] = store.company_engagement(key, pb.name, exclude_run_id=getattr(self, "run_id", ""))
        eng = company_state[key]
        if eng["stages"] & {Stage.REPLIED, Stage.POSITIVE, Stage.BOOKED, Stage.WON, Stage.LOST}:
            return "company already engaged (replied / lost) - not re-contacted"
        if ct and ct.email:
            if store.is_suppressed(email=ct.email, domain=lead.company.domain):
                return "suppressed"
            if excluded_domain(ct.email, ctx):
                return "email at an excluded domain"
            if store.was_exported(lead.id):
                return "already handed over"
            dedupe_days = int(pb.outbound.get("dedupe_days") or 0)
            if store.recently_contacted(ct.email, dedupe_days, ctx.today, exclude_lead_id=lead.id):
                return f"contacted in the last {dedupe_days} days"
        cooldown = int(pb.outbound.get("company_cooldown_days") or 0)
        last = eng["last_exported"]
        if cooldown and last and (ctx.today - last).days < cooldown:
            return f"company contacted {(ctx.today - last).days}d ago (cooldown {cooldown}d)"
        return None

    def outbound_leads(self, leads: List[Lead]) -> List[Lead]:
        """Leads that may be handed to a sending tool (best score first, one per email)."""
        tiers = set(self.pb.outbound.get("tiers") or [Tier.HOT, Tier.NORMAL])
        company_state: Dict[str, Dict[str, Any]] = {}
        seen_emails: set = set()
        out = []
        for lead in leads:
            if lead.tier not in tiers or not lead.messages:
                continue
            reason = self.block_reason(lead, company_state)
            if reason:
                if reason not in lead.notes:
                    lead.notes.append(reason)
                continue
            ct = lead.contact
            if ct and ct.email:
                em = ct.email.strip().lower()
                if em in seen_emails:
                    lead.notes.append("same email as a higher-scored lead in this run - not re-contacted")
                    continue
                seen_emails.add(em)
            out.append(lead)
        return out

    def _write_run_files(self, out_dir: Path, leads: List[Lead], counts: Dict[str, int],
                         usage: Optional[List[Dict[str, Any]]] = None) -> None:
        with open(out_dir / "rejected.csv", "w", newline="", encoding="utf-8") as f:
            from .outbound.csv_export import guard_cell  # company names come from scraped sources
            w = csv.DictWriter(f, fieldnames=["company", "domain", "stage", "reason"])
            w.writeheader()
            w.writerows({k: guard_cell("" if r.get(k) is None else str(r.get(k)))
                         for k in ("company", "domain", "stage", "reason")} for r in self.rejected)
        (out_dir / "summary.json").write_text(json.dumps({
            "playbook": self.pb.name, "mode": self.pb.mode, "counts": counts, "errors": self.errors,
            "warnings": self.warnings, "usage": usage or [],
            "top": [{"company": ld.company.name, "score": ld.score, "tier": ld.tier,
                     "contact": ld.contact.full_name if ld.contact else ""} for ld in leads[:20]],
        }, indent=2), encoding="utf-8")
