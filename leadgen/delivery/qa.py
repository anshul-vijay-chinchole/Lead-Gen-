"""The QA summary printed after every client delivery.

``build_qa`` looks at one delivery - the pipeline run, the package that was
written and what the ledger removed - and returns a ``QAReport``: how many
companies were found, how many survived each step, why the rest were left out
(grouped reasons), how many duplicates were removed, how trustworthy the emails
are, what the run cost, and plain-English warnings when something needs a look
before the files go to the client. ``deliver()`` (``run.py``) prints
``QAReport.lines()`` and saves them to ``<folder>/_internal/qa.txt`` and
``QAReport.to_dict()`` to ``qa.json``.

What is counted
---------------
leads_found          companies sourced (after merging duplicates across sources)
with_signal          companies with a live signal (fresh, matching the client's roles)
qualified            companies that match the client's criteria (ICP filter)
delivered            rows in the file (at most ``leads_per_week``)
target               the client's ``leads_per_week``
filtered_out         companies / leads left out by a filter: every pipeline rejection
                     (signals, ICP, already delivered / do-not-list) plus the
                     delivery selection's filters (urgency tier, no live job posting).
                     Duplicates inside the run and leads held back by the weekly
                     limit are counted separately.
top_reasons          the most common reasons, grouped: details in parentheses,
                     ``[...]`` lists, dates and counts are removed, so
                     "too small (12 employees, min 20)" and "too small (5 employees,
                     min 20)" are one reason, "too small" (see ``group_reason``).
duplicates_removed   companies, jobs and contacts the ledger removed because the client
                     already has them + leads that repeated a company / person that is
                     already in this delivery.
suppressed           companies / people removed by a do-not-list: the client's own
                     (``leadgen suppress --client``, ``exclusions.companies`` - the
                     ledger hooks' ``suppressed`` count), the client file's
                     ``exclusions.domains`` / ``keywords`` (ICP rejections "excluded
                     domain" / "excluded keyword" that one of them caused; the base
                     playbook's own exclusions are criteria, not the client's
                     do-not-list), and the global suppression list (companies:
                     "... is on the suppression list" rejections; people: the
                     pipeline's ``counts["suppressed_contacts"]`` when it reports it).
                     These companies are also counted in ``filtered_out``.
verified_email_rate  rows with a "verified" email / rows delivered (0..1).
email_status_counts  rows per email status (verified, risky, guessed-unverified, not found).
warnings             volume below target, no leads at all, paid-lookup budget reached,
                     source errors, other run errors, AI opening-line cost cap reached,
                     plus anything the caller adds (e.g. a failed Google Sheets push).
usage_lines          API usage + estimated cost (``UsageMeter.summary_lines``).

Client settings read: ``name``, ``display_name``, ``leads_per_week``,
``exclusions.domains`` / ``.keywords`` (for ``suppressed``) and
``opening_line.max_cost_usd`` (for the cost-cap warning). Nothing else is read
from the client or the playbook.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..signals import as_str_list, keyword_match
from ..utils import normalize_domain
from .rows import EMAIL_LABELS, VERIFIED, DeliveryPackage

TOP_REASONS = 5            # how many grouped reasons the report lists
MAX_SOURCE_WARNINGS = 5    # source errors listed one by one (the rest are counted)

# selection entries (``not_delivered``) carry one of these kinds
KIND_FILTERED = "filtered"       # left out by a delivery filter (tier, no job posting)
KIND_DUPLICATE = "duplicate"     # same company / person as a higher-ranked lead in this run
KIND_OVER_LIMIT = "over_limit"   # qualified, but over the client's weekly limit


# --- reason grouping ------------------------------------------------------------------

_PARENS_RE = re.compile(r"\s*\([^()]*\)")
_BRACKETS_RE = re.compile(r"\[[^\[\]]*\]")
_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
# a number that is not part of a time window such as "in last 7 days" (those are kept:
# the window is the same for every company of a run, so it groups fine and says a lot)
_COUNT_RE = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?(?![\w.])(?!\s*(?:days?|weeks?)\b)")
_SPECIFIC: Sequence[Tuple["re.Pattern[str]", str]] = (
    (re.compile(r"^domain \S+ is on the suppression list$"), "domain is on the suppression list"),
    (re.compile(r"^company .+ is on the suppression list$"), "company is on the suppression list"),
    (re.compile(r"^excluded domain \S+.*$"), "excluded domain"),
    (re.compile(r"^company name .+ matches excluded pattern .+$"), "company name matches an excluded pattern"),
    (re.compile(r"^email at an excluded domain.*$"), "email at an excluded domain"),
)


def group_reason(reason: Any) -> str:
    """The grouping label of a rejection reason (see the module docstring).

    ``"too small (12 employees, min 20)"`` -> ``"too small"``;
    ``"no signal matching [accountant, controller] in last 7 days (3 signals found: ...)"``
    -> ``"no signal matching [...] in last 7 days"``;
    ``"already delivered to this client (2026-09-17)"`` -> ``"already delivered to this client"``.
    """
    text = re.sub(r"\s+", " ", str(reason or "")).strip()
    previous = None
    while previous != text:                  # nested parentheses: innermost first
        previous = text
        text = _PARENS_RE.sub("", text)
    text = _BRACKETS_RE.sub("[...]", text)
    text = _ISO_DATE_RE.sub("", text)
    for pattern, label in _SPECIFIC:
        if pattern.match(text):
            text = label
            break
    text = _COUNT_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" -:;,.")
    return text or "other"


def top_reasons(reasons: Iterable[Any], n: int = TOP_REASONS) -> List[Tuple[str, int]]:
    """The ``n`` most common grouped reasons as ``(reason, count)``, most common first."""
    counts = Counter(group_reason(r) for r in reasons)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[: max(0, int(n))]


# --- the report ---------------------------------------------------------------------------

@dataclass
class QAReport:
    """What happened in one delivery (see the module docstring for every count)."""

    client: str
    leads_found: int = 0
    with_signal: int = 0
    qualified: int = 0
    delivered: int = 0
    target: int = 0
    filtered_out: int = 0
    top_reasons: List[Tuple[str, int]] = field(default_factory=list)
    duplicates_removed: int = 0
    suppressed: int = 0
    verified_email_rate: float = 0.0
    email_status_counts: Dict[str, int] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    usage_lines: List[str] = field(default_factory=list)
    # --- details (not required by the contract; shown when present) ---
    client_display: str = ""
    date: str = ""                                   # delivery date, YYYY-MM-DD
    run_id: str = ""
    dry_run: bool = False
    duplicates: Dict[str, int] = field(default_factory=dict)   # breakdown of duplicates_removed
    held_back: int = 0                               # qualified leads over the weekly limit
    hot: int = 0
    companies: int = 0
    opening: Dict[str, Any] = field(default_factory=dict)      # OpeningStats as a dict ({} = column off)
    paid_lookups: int = 0
    max_paid_lookups: int = 0                        # 0 = no cap
    estimated_cost_usd: Optional[float] = None
    notes: List[str] = field(default_factory=list)   # information (not problems)
    folder: str = ""
    files: Dict[str, str] = field(default_factory=dict)
    sheet_url: str = ""

    @property
    def below_target(self) -> bool:
        """True when fewer leads were delivered than the client's weekly target."""
        return self.delivered < self.target

    @property
    def verified_emails(self) -> int:
        return int(self.email_status_counts.get(VERIFIED, 0) or 0)

    def lines(self) -> List[str]:
        """The summary as printable lines (printed after each run, saved as qa.txt)."""
        who = self.client_display or self.client
        if self.client_display and self.client_display != self.client:
            who = f"{self.client_display} ({self.client})"
        head = f"Delivery QA - {who}"
        if self.date:
            head += f" - {self.date}"
        if self.dry_run:
            head += " - DRY RUN (preview only)"
        out = [head]
        if self.dry_run:
            out.append("  preview: nothing was recorded as delivered, files are named ...-PREVIEW")
        out += [
            f"  companies found ............ {self.leads_found}",
            f"  with a live signal ......... {self.with_signal}",
            f"  match the client's criteria  {self.qualified}",
            f"  delivered .................. {self.delivered} (target {self.target})"
            + (f", {self.hot} hot" if self.delivered else ""),
            f"  filtered out ............... {self.filtered_out}",
        ]
        if self.top_reasons:
            out.append("    top reasons:")
            width = max(len(str(n)) for _, n in self.top_reasons)
            out += [f"      {str(n).rjust(width)}  {reason}" for reason, n in self.top_reasons]
        out.append(f"  duplicates removed ......... {self.duplicates_removed}"
                   + (f" ({self._duplicates_detail()})" if self.duplicates_removed else ""))
        out.append(f"  on a do-not-list ........... {self.suppressed}")
        if self.held_back:
            out.append(f"  held back (over the limit) . {self.held_back} - not recorded, so they can go "
                       f"in a later delivery")
        if self.delivered:
            out.append(f"  verified email rate ........ {self.verified_email_rate:.0%} "
                       f"({self.verified_emails} of {self.delivered})")
        else:
            out.append("  verified email rate ........ n/a (no leads)")
        statuses = ", ".join(f"{k} {self.email_status_counts.get(k, 0)}" for k in EMAIL_LABELS)
        out.append(f"  email status ............... {statuses}")
        if self.opening:
            o = self.opening
            line = (f"  opening lines .............. {o.get('ai_lines', 0)} AI, "
                    f"{o.get('template_lines', 0)} template")
            if o.get("ai_lines") or o.get("errors") or o.get("cost_usd"):
                line += f", ~${float(o.get('cost_usd') or 0):.4f}"
            if o.get("errors"):
                line += f", {o.get('errors')} AI error(s)"
            out.append(line)
        out += [f"  {u}" for u in self.usage_lines]
        if self.files:
            out.append(f"  files ({self.folder}):" if self.folder else "  files:")
            out += [f"    {fmt:<5} {path}" for fmt, path in self.files.items()]
        if self.sheet_url:
            out.append(f"  google sheet: {self.sheet_url}")
        out += [f"  note: {n}" for n in self.notes]
        out += [f"  WARNING: {w}" for w in self.warnings]
        return out

    def text(self) -> str:
        """``lines()`` joined with newlines."""
        return "\n".join(self.lines())

    def _duplicates_detail(self) -> str:
        d = self.duplicates or {}
        parts: List[str] = []
        before = [(d.get("company", 0), "company", "companies"), (d.get("job", 0), "job", "jobs"),
                  (d.get("contact", 0), "contact", "contacts")]
        shown = [f"{n} {one if n == 1 else many}" for n, one, many in before if n]
        if shown:
            parts.append("already delivered: " + ", ".join(shown))
        in_run = int(d.get("same_company_in_run", 0) or 0) + int(d.get("same_contact_in_run", 0) or 0) \
            + int(d.get("in_run", 0) or 0)
        if in_run:
            parts.append(f"repeated in this run: {in_run}")
        return "; ".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        """Plain JSON-ready dict (qa.json)."""
        return {
            "client": self.client,
            "client_display": self.client_display,
            "date": self.date,
            "run_id": self.run_id,
            "dry_run": self.dry_run,
            "leads_found": self.leads_found,
            "with_signal": self.with_signal,
            "qualified": self.qualified,
            "delivered": self.delivered,
            "target": self.target,
            "below_target": self.below_target,
            "hot": self.hot,
            "companies": self.companies,
            "filtered_out": self.filtered_out,
            "top_reasons": [[reason, n] for reason, n in self.top_reasons],
            "duplicates_removed": self.duplicates_removed,
            "duplicates": dict(self.duplicates),
            "suppressed": self.suppressed,
            "held_back": self.held_back,
            "verified_email_rate": round(float(self.verified_email_rate), 4),
            "email_status_counts": dict(self.email_status_counts),
            "opening": dict(self.opening),
            "paid_lookups": self.paid_lookups,
            "max_paid_lookups": self.max_paid_lookups,
            "estimated_cost_usd": self.estimated_cost_usd,
            "warnings": list(self.warnings),
            "notes": list(self.notes),
            "usage_lines": list(self.usage_lines),
            "folder": self.folder,
            "files": dict(self.files),
            "sheet_url": self.sheet_url,
        }


# --- building it ------------------------------------------------------------------------------

def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """``obj.key`` or ``obj[key]`` (clients / stats may be objects or mappings)."""
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _opening_dict(stats: Any) -> Dict[str, Any]:
    if stats is None:
        return {}
    return {
        "ai_lines": _int(_get(stats, "ai_lines")),
        "template_lines": _int(_get(stats, "template_lines")),
        "cost_usd": round(float(_get(stats, "cost_usd", 0.0) or 0.0), 6),
        "capped": bool(_get(stats, "capped", False)),
        "errors": _int(_get(stats, "errors")),
        "notes": [str(n) for n in (_get(stats, "notes") or [])],
    }


def _within_run(within_run_dupes: Any) -> Dict[str, int]:
    if within_run_dupes is None:
        return {}
    if isinstance(within_run_dupes, Mapping):
        out = {}
        for key, name in (("company", "same_company_in_run"), ("contact", "same_contact_in_run")):
            if _int(within_run_dupes.get(key)):
                out[name] = _int(within_run_dupes.get(key))
        other = sum(_int(v) for k, v in within_run_dupes.items() if k not in ("company", "contact"))
        if other:
            out["in_run"] = other
        return out
    n = _int(within_run_dupes)
    return {"in_run": n} if n else {}


# check_icp's reasons for the exclusion lists (``filters.check_icp``)
_EXCLUDED_DOMAIN_RE = re.compile(r"^excluded domain (\S+)")
_EXCLUDED_KEYWORD_RE = re.compile(r"^excluded keyword (?P<q>['\"])(?P<kw>.+)(?P=q) in \w+$")


def _client_exclusions(client: Any) -> Tuple[List[str], List[str]]:
    """(normalised domains, keywords) of the client file's ``exclusions``."""
    ex = _get(client, "exclusions", None) if not isinstance(client, str) else None
    if ex is None:
        return [], []
    domains = [d for d in (normalize_domain(x) for x in as_str_list(_get(ex, "domains", None))) if d]
    return domains, as_str_list(_get(ex, "keywords", None))


def _by_client_exclusion(reason: Any, domains: Sequence[str], keywords: Sequence[str]) -> bool:
    """True when an ICP rejection comes from the client file's ``exclusions.domains`` /
    ``keywords`` (they are merged with the base playbook's lists, so check which)."""
    text = str(reason or "").strip()
    m = _EXCLUDED_DOMAIN_RE.match(text)
    if m and domains:
        dom = normalize_domain(m.group(1))
        return bool(dom) and any(dom == d or dom.endswith("." + d) for d in domains)
    m = _EXCLUDED_KEYWORD_RE.match(text)
    if m and keywords:
        # the base playbook's keyword may have matched first ("staffing agency"): the client's
        # own ("staffing") counts when it would have matched that same phrase
        return keyword_match(m.group("kw"), keywords) is not None
    return False


def _is_budget_text(text: str) -> bool:
    t = text.lower()
    return "budget" in t and ("paid" in t or "lookup" in t)


def _usd(value: float) -> str:
    """'$0.50', '$12.00' - or '$0.005' when cents would hide the amount."""
    if value >= 0.01 and abs(value * 100 - round(value * 100)) < 1e-9:
        return f"${value:,.2f}"
    return "$" + (f"{value:.6f}".rstrip("0").rstrip(".") or "0")


def _low_volume_hint(report: QAReport) -> str:
    dup = report.duplicates_removed
    if dup and dup * 2 >= max(1, report.qualified):
        return ("Most matches were already delivered to this client in earlier weeks. To find more, widen "
                "the roles / locations, raise freshness_days or add sources.")
    return "To find more, widen the roles / locations, raise freshness_days or add sources."


def build_qa(client: Any, run_result: Any, pkg: DeliveryPackage, ledger_removed: Optional[Mapping[str, Any]] = None,
             within_run_dupes: Any = None, opening_stats: Any = None, usage: Any = None, *,
             not_delivered: Optional[Sequence[Mapping[str, Any]]] = None, dry_run: bool = False,
             extra_warnings: Sequence[str] = (), notes: Sequence[str] = ()) -> QAReport:
    """Build the QA report of one delivery.

    ``client``            the ``Client`` (or anything with ``name`` / ``leads_per_week``).
    ``run_result``        the pipeline's ``RunResult`` (counts, rejected, errors, warnings, usage).
    ``pkg``               the ``DeliveryPackage`` that was written.
    ``ledger_removed``    ``LedgerHooks.removed`` (``{"company", "job", "contact", "suppressed"}``).
    ``within_run_dupes``  leads dropped because their company / person was already in this
                          delivery: an int, or ``{"company": n, "contact": n}``.
    ``opening_stats``     ``OpeningStats`` when the opening-line column is on (else None).
    ``usage``             the run's ``UsageMeter`` (default: the run's own usage lines).
    ``not_delivered``     the delivery selection's drops: dicts with ``reason`` and ``kind``
                          (``filtered`` / ``duplicate`` / ``over_limit``); ``filtered`` ones
                          count as filtered out, ``over_limit`` ones as held back.
    ``dry_run``           marks the report as a preview.
    ``extra_warnings``    added to the warnings as given (e.g. a failed Google Sheets push).
    ``notes``             information lines shown after the numbers.
    """
    name = str(client if isinstance(client, str) else (_get(client, "name", "") or ""))
    display = str(_get(client, "display_name", "") or name)
    target = _int(_get(client, "leads_per_week", 0))
    counts: Mapping[str, Any] = getattr(run_result, "counts", None) or {}
    rejected = list(getattr(run_result, "rejected", None) or [])
    selection = list(not_delivered or [])
    filtered_sel = [e for e in selection if str(e.get("kind") or KIND_FILTERED) == KIND_FILTERED]
    held_back = sum(1 for e in selection if e.get("kind") == KIND_OVER_LIMIT)

    reasons = [r.get("reason", "") for r in rejected] + [e.get("reason", "") for e in filtered_sel]

    removed = {k: _int((ledger_removed or {}).get(k)) for k in ("company", "job", "contact", "suppressed")}
    duplicates: Dict[str, int] = {k: removed[k] for k in ("company", "job", "contact") if removed[k]}
    duplicates.update(_within_run(within_run_dupes))
    globally_suppressed = sum(1 for r in rejected if "suppression list" in str(r.get("reason", "")))
    ex_domains, ex_keywords = _client_exclusions(client)
    client_excluded = sum(1 for r in rejected if _by_client_exclusion(r.get("reason"), ex_domains, ex_keywords))
    people_suppressed = _int(counts.get("suppressed_contacts"))

    rows = list(getattr(pkg, "rows", None) or [])
    delivered = len(rows)
    status_counts = dict(pkg.counts_by_email_status) if pkg is not None else {k: 0 for k in EMAIL_LABELS}
    verified = _int(status_counts.get(VERIFIED))
    companies = len({r.get("_company_key") or str(r.get("company", "")).lower() for r in rows})

    report = QAReport(
        client=name,
        client_display=display,
        date=pkg.period_end.isoformat() if pkg is not None and pkg.period_end else "",
        run_id=str(getattr(run_result, "run_id", "") or getattr(pkg, "run_id", "") or ""),
        dry_run=bool(dry_run),
        leads_found=_int(counts.get("sourced")),
        with_signal=_int(counts.get("with_signal")),
        qualified=_int(counts.get("qualified")),
        delivered=delivered,
        target=target,
        filtered_out=len(rejected) + len(filtered_sel),
        top_reasons=top_reasons(reasons),
        duplicates_removed=sum(duplicates.values()),
        duplicates=duplicates,
        suppressed=removed["suppressed"] + client_excluded + globally_suppressed + people_suppressed,
        held_back=held_back,
        verified_email_rate=(verified / delivered) if delivered else 0.0,
        email_status_counts={k: _int(status_counts.get(k)) for k in EMAIL_LABELS},
        hot=sum(1 for r in rows if r.get("urgency") == "hot"),
        companies=companies,
        opening=_opening_dict(opening_stats),
        notes=[str(n) for n in notes if str(n).strip()],
    )

    # --- usage + cost -------------------------------------------------------------------
    blocked = 0
    if usage is not None and hasattr(usage, "summary_lines"):
        report.usage_lines = list(usage.summary_lines())
        report.paid_lookups = _int(getattr(usage, "paid_lookups", 0))
        report.max_paid_lookups = _int(getattr(usage, "max_paid_lookups", 0))
        report.estimated_cost_usd = getattr(usage, "estimated_cost", None)
        blocked = sum(_int(getattr(u, "blocked", 0)) for u in (getattr(usage, "adapters", {}) or {}).values())
    else:
        report.usage_lines = [str(u) for u in (getattr(run_result, "usage", None) or [])]
        report.paid_lookups = _int(counts.get("paid_lookups"))

    # --- warnings ---------------------------------------------------------------------------
    warnings: List[str] = []
    if delivered == 0:
        warnings.append("No leads in this delivery - the files are empty. Don't send them to the client; "
                        "check the reasons above (and whether the sources returned anything) first.")
    if delivered < target:
        warnings.append(f"Low volume: {delivered} of {target} leads delivered (below this client's weekly "
                        f"target). {_low_volume_hint(report)}")

    run_warnings = [str(w) for w in (getattr(run_result, "warnings", None) or [])]
    opening_notes = report.opening.get("notes", []) if report.opening else []
    budget_hit = blocked > 0 or any(_is_budget_text(w) for w in run_warnings) or any(
        _is_budget_text(n) for n in opening_notes)
    if budget_hit:
        cap = f"{report.paid_lookups} of {report.max_paid_lookups}" if report.max_paid_lookups \
            else str(report.paid_lookups)
        warnings.append(f"Paid-lookup budget reached ({cap} paid lookups used): some contacts / emails were not "
                        f"looked up with the paid providers. Raise budget.max_paid_lookups in the client file "
                        f"or run with --budget N.")
    elif report.max_paid_lookups and report.paid_lookups >= report.max_paid_lookups:
        report.notes.append(f"all {report.max_paid_lookups} paid lookups of the budget were used")

    errors = [str(e) for e in (getattr(run_result, "errors", None) or [])]
    source_errors = [e for e in errors if e.startswith("source ")]
    for e in source_errors[:MAX_SOURCE_WARNINGS]:
        warnings.append(f"Source problem - {e}. Leads from this source are missing this week.")
    if len(source_errors) > MAX_SOURCE_WARNINGS:
        warnings.append(f"... and {len(source_errors) - MAX_SOURCE_WARNINGS} more source problem(s).")
    other_errors = [e for e in errors if not e.startswith("source ")]
    if other_errors:
        where = getattr(run_result, "out_dir", "")
        detail = f" - details in {where}/summary.json" if where else ""
        warnings.append(f"{len(other_errors)} other error(s) during the run, e.g. {other_errors[0]}{detail}")

    cap_warned = False
    if report.opening.get("capped"):
        cap_usd = _get(_get(client, "opening_line", {}) or {}, "max_cost_usd", None)
        try:
            cap_value: Optional[float] = float(cap_usd) if cap_usd is not None else None
        except (TypeError, ValueError):
            cap_value = None
        if cap_value is None or cap_value > 0:   # a cap of 0 is a choice (template only), not a problem
            cap_text = _usd(cap_value) if cap_value is not None else "the cost cap"
            warnings.append(f"AI opening-line cost cap reached ({cap_text}): "
                            f"{report.opening.get('template_lines', 0)} line(s) use the free template "
                            f"instead. Raise opening_line.max_cost_usd in the client file to allow more "
                            f"AI lines.")
            cap_warned = True

    warnings += [str(w) for w in extra_warnings if str(w).strip()]
    for w in run_warnings:           # anything else the pipeline flagged
        if not _is_budget_text(w):
            warnings.append(w)
    for n in opening_notes:
        if _is_budget_text(n) or (cap_warned and "cost cap" in n.lower()) or n in report.notes:
            continue
        report.notes.append(n)

    for w in warnings:               # each warning once, in order
        if w not in report.warnings:
            report.warnings.append(w)
    return report


__all__ = ["KIND_DUPLICATE", "KIND_FILTERED", "KIND_OVER_LIMIT", "QAReport", "TOP_REASONS", "build_qa",
           "group_reason", "top_reasons"]
