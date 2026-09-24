"""The factory line: SOURCE -> SIGNALS -> ICP FILTER -> ENRICH -> VERIFY -> SCORE -> WRITE -> EXPORT.

``Pipeline(ctx).run()`` executes one full run for the playbook in ``ctx`` and
returns a ``RunResult``. Every stage is defensive: one broken source or a
missing API key logs an error and the run carries on with what it has.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import registry
from .context import Context, MissingCredentialError
from .contacts import ContactWaterfall, select_contacts
from .filters import apply_icp, excluded_domain
from .http import HttpError, redact
from .models import Company, Contact, EmailStatus, Lead, Stage, Tier
from .notify import notify
from .outbound.base import ExportResult
from .scoring import score, tier_for
from .signals import process_signals
from .utils import normalize_company_name
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

    def summary(self) -> str:
        c = self.counts
        lines = [
            f"Run {self.run_id} ({self.playbook})",
            f"  sourced companies ....... {c.get('sourced', 0)}",
            f"  with a live signal ...... {c.get('with_signal', 0)}",
            f"  match the ICP ........... {c.get('qualified', 0)}",
            f"  decision-maker found .... {c.get('enriched', 0)}",
            f"  email verified .......... {c.get('verified', 0)}",
            f"  hot / normal / skip ..... {c.get('hot', 0)} / {c.get('normal', 0)} / {c.get('skip', 0)}",
            f"  sequences written ....... {c.get('written', 0)}",
            f"  handed to outbound ...... {c.get('exported', 0)}",
            f"  output .................. {self.out_dir}",
        ]
        for e in self.exports:
            lines.append(f"    - {e.exporter}: {e.count} {e.path or e.detail}".rstrip())
        if self.errors:
            lines.append(f"  errors ({len(self.errors)}):")
            lines += [f"    ! {e}" for e in self.errors]
        return "\n".join(lines)


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
    def __init__(self, ctx: Context, out_dir: Optional[Path] = None, limit: Optional[int] = None):
        self.ctx = ctx
        self.pb = ctx.playbook
        self.limit = limit
        self.base_out = Path(out_dir) if out_dir else Path("output") / self.pb.name
        self.errors: List[str] = []
        self.rejected: List[Dict[str, str]] = []

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
            except (MissingCredentialError, HttpError, registry.UnknownAdapterError, OSError, ValueError) as e:
                self._err(f"source {label}", e)
        return merge_companies(companies)

    def _verifier(self) -> Any:
        cfg = self.pb.enrichment.get("verifier")
        if not cfg:
            return None
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

    def verify_contact(self, contact: Contact, verifier: Any) -> None:
        """Set contact.email/email_status, trying guessed candidates if needed."""
        store = self.ctx.store
        accept = set(self.pb.enrichment.get("accept_statuses") or [])
        candidates = [contact.email] if contact.email else []
        candidates += [e for e in contact.email_candidates if e and e not in candidates]
        if not candidates or verifier is None:
            return
        best: Optional[Tuple[str, str]] = None
        for email in candidates[: max(1, MAX_CANDIDATES_TO_VERIFY)]:
            status = store.get_verification(email, today=self.ctx.today) if store else None
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
        for i, (pre, company) in enumerate(prescored):
            if i < budget and pre >= min_pre:
                try:
                    waterfall.enrich(company)
                except Exception as e:  # noqa: BLE001 - never lose the run to one company
                    self._err(f"enrich {company.name}", e)
            chosen = select_contacts(company, ctx, limit=per_company * 3)
            accepted: List[Contact] = []
            good = 0
            for contact in chosen:
                if good >= per_company:
                    break  # enough deliverable people: don't spend verification credits on the rest
                if store.is_suppressed(email=contact.email):
                    continue
                if not contact.email or contact.email_status == EmailStatus.UNKNOWN:
                    self.verify_contact(contact, verifier)
                    if contact.email and store.is_suppressed(email=contact.email):
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
                if self.email_ok(ct):
                    lead.stage = Stage.VERIFIED
                elif ct.email and ct.data.get("email_guessed") and ct.email_status in accept:
                    lead.notes.append(f"guessed email only {ct.email_status} (needs valid)")
                elif ct.email:
                    lead.notes.append(f"email {ct.email_status}")
                else:
                    lead.notes.append("no email found")
                if ct.email and store.recently_contacted(ct.email, dedupe_days, ctx.today,
                                                         exclude_lead_id=lead.id):
                    lead.notes.append(f"contacted in the last {dedupe_days} days")
            else:
                lead.notes.append("no decision-maker found")
        leads.sort(key=lambda ld: ld.score, reverse=True)
        counts["verified"] = sum(1 for ld in leads if ld.stage == Stage.VERIFIED)
        for t in (Tier.HOT, Tier.NORMAL, Tier.SKIP):
            counts[t] = sum(1 for ld in leads if ld.tier == t)

        # 8. WRITE
        written = 0
        w_tiers = set(pb.writer.get("tiers") or [Tier.HOT, Tier.NORMAL])
        max_write = int(pb.writer.get("max_leads") or 0) or len(leads)
        writer = build_writer(ctx)
        written_emails: set = set()
        for lead in leads:
            if written >= max_write:
                break
            if lead.tier not in w_tiers or not self.email_ok(lead.contact):
                continue
            em = lead.contact.email.strip().lower()
            if em in written_emails:  # same person under two company records: keep the best-scored
                lead.notes.append("same email as a higher-scored lead in this run")
                continue
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
        # afterwards show the final stage of every lead.
        outbound = self.outbound_leads(leads)
        exports: List[ExportResult] = []
        handed: set = set()
        built = []
        for cfg in pb.outbound.get("exporters") or []:
            if cfg.get("enabled") is False:
                continue
            try:
                exp = self._make("exporter", cfg)
            except Exception as e:  # noqa: BLE001
                self._err(f"exporter {cfg.get('type')}", e)
                continue
            if exp is not None:
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

        self._write_run_files(out_dir, leads, counts)
        store.finish_run(run_id, counts)
        result = RunResult(run_id=run_id, playbook=pb.name, out_dir=out_dir, counts=counts,
                           leads=leads, rejected=self.rejected, exports=exports, errors=self.errors)
        notify(ctx, "run_summary", f"Run finished: {pb.name}", result.summary(),
               {"run_id": run_id, "counts": counts})
        return result

    def outbound_leads(self, leads: List[Lead]) -> List[Lead]:
        """Leads that may be handed to a sending tool."""
        pb, store, ctx = self.pb, self.ctx.store, self.ctx
        tiers = set(pb.outbound.get("tiers") or [Tier.HOT, Tier.NORMAL])
        seen_emails: set = set()
        dedupe_days = int(pb.outbound.get("dedupe_days") or 0)
        cooldown = int(pb.outbound.get("company_cooldown_days") or 0)
        engaged_stages = {Stage.REPLIED, Stage.POSITIVE, Stage.BOOKED, Stage.WON, Stage.LOST}
        run_id = getattr(self, "run_id", "")
        company_state: Dict[str, Dict[str, Any]] = {}
        out = []
        for lead in leads:
            ct = lead.contact
            if lead.tier not in tiers or not lead.messages:
                continue
            key = lead.company.key
            if key not in company_state:
                company_state[key] = store.company_engagement(key, pb.name, exclude_run_id=run_id)
            eng = company_state[key]
            if eng["stages"] & engaged_stages:
                lead.notes.append("company already engaged (replied / lost) - not re-contacted")
                continue
            last = eng["last_exported"]
            if cooldown and last and (ctx.today - last).days < cooldown and not store.was_exported(lead.id):
                lead.notes.append(f"company contacted {(ctx.today - last).days}d ago (cooldown {cooldown}d)")
                continue
            if pb.outbound.get("require_email", True) and not self.email_ok(ct):
                continue
            if ct and ct.email:
                if store.is_suppressed(email=ct.email, domain=lead.company.domain):
                    continue
                if excluded_domain(ct.email, ctx):
                    continue
                if store.was_exported(lead.id):
                    continue
                if store.recently_contacted(ct.email, dedupe_days, ctx.today, exclude_lead_id=lead.id):
                    continue
                em = ct.email.strip().lower()
                if em in seen_emails:
                    lead.notes.append("same email as a higher-scored lead in this run - not re-contacted")
                    continue
                seen_emails.add(em)
            out.append(lead)
        return out

    def _write_run_files(self, out_dir: Path, leads: List[Lead], counts: Dict[str, int]) -> None:
        with open(out_dir / "rejected.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["company", "domain", "stage", "reason"])
            w.writeheader()
            w.writerows(self.rejected)
        (out_dir / "summary.json").write_text(json.dumps({
            "playbook": self.pb.name, "counts": counts, "errors": self.errors,
            "top": [{"company": ld.company.name, "score": ld.score, "tier": ld.tier,
                     "contact": ld.contact.full_name if ld.contact else ""} for ld in leads[:20]],
        }, indent=2), encoding="utf-8")
