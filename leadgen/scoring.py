"""Stage 7 - SCORE: turn a company + chosen contact into a 0-100 score and a tier.

``score(company, contact, ctx)`` returns a ``ScoreBreakdown`` with four
components (each capped by its weight) and short human-readable reasons:

* **intent** (max ``weights.intent``) - how strong and fresh the *primary*
  buying signal is. Sub-points from ``scoring.intent``:

  - ``fresh``: freshest primary signal's age banded by
    ``scoring.freshness_days`` (default ``[3, 7, 14, 30]``): within the first
    band full credit, then 3/4, 1/2, 1/4 (generally ``(n - i) / n`` for band
    ``i`` of ``n``), older than the last band 0; age unknown 1/4.
  - ``volume``: ``(volume - 1) / (volume_full_at - 1)`` capped at 1 (one
    signal earns nothing; ``volume_full_at`` signals earn full points). If
    ``volume_full_at`` <= 1, any primary signal earns full points.
  - ``persistence``: full points when a primary signal was re-posted or is
    at least ``signals.stale_after_days`` old (a hard-to-fill need).
  - ``urgency``: full points when a primary signal uses
    ``signals.urgency_keywords``.

  intent = earned / sum(sub-points) * weights.intent; no primary signal = 0.
* **fit** (max ``weights.fit``) - mean over ``filters.fit_checks`` (location,
  size, industry) of 1 (match or not configured) / 0 (mismatch) /
  ``scoring.unknown_credit`` (data unknown), times ``weights.fit``.
* **reachability** (max ``weights.reachability``) - half *person*, half
  *email*. Person: no contact 0; excluded title 0; ``buyers.titles`` empty
  0.8; buyer #1 1.0, #2-#3 0.8, lower-priority buyer 0.6, a non-target title
  0.3. Email: valid 1, risky (catch-all) 0.5, unverified 0.3, missing or
  invalid 0.
* **extra** (max ``weights.extra``) - ``scoring.extra_per_signal`` for every
  distinct secondary signal type (funding, leadership change, ...).

``tier_for(total, ctx)`` maps the total to ``hot`` (>= ``tiers.hot``),
``normal`` (>= ``tiers.normal``) or ``skip``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .contacts import is_excluded_title, title_rank
from .filters import fit_report
from .models import Company, Contact, EmailStatus, ScoreBreakdown, Tier
from .signals import as_str_list, signal_stats

_DEFAULT_WEIGHTS = {"intent": 40, "fit": 30, "reachability": 20, "extra": 10}
_DEFAULT_INTENT = {"fresh": 15, "volume": 10, "persistence": 10, "urgency": 5}
_DEFAULT_BANDS = [3, 7, 14, 30]


def _num(value: Any, default: float = 0.0) -> float:
    """Non-negative float from a config value (bad values fall back to ``default``)."""
    if isinstance(value, bool):
        return default
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if f >= 0 else default


def _weights(cfg: Dict[str, Any]) -> Dict[str, float]:
    raw = cfg.get("weights") or {}
    return {k: _num(raw.get(k, d), float(d)) for k, d in _DEFAULT_WEIGHTS.items()}


def _bands(cfg: Dict[str, Any]) -> List[float]:
    raw = cfg.get("freshness_days")
    if not isinstance(raw, (list, tuple)) or not raw:
        raw = _DEFAULT_BANDS
    out = sorted(_num(v, -1.0) for v in raw)
    return [v for v in out if v >= 0] or [float(b) for b in _DEFAULT_BANDS]


def freshness_credit(age: Optional[int], bands: List[float]) -> float:
    """Share of the ``fresh`` points for a signal ``age`` days old."""
    if age is None:
        return 0.25
    n = len(bands)
    for i, limit in enumerate(bands):
        if age <= limit:
            return (n - i) / n
    return 0.0


def volume_credit(volume: int, full_at: Any) -> float:
    """Share of the ``volume`` points for ``volume`` primary signals."""
    full = _num(full_at, 3.0)
    if full <= 1:
        return 1.0 if volume >= 1 else 0.0
    if volume <= 1:
        return 0.0
    return min(1.0, (volume - 1) / (full - 1))


def _open_days(stats: Dict[str, Any], today: Any) -> int:
    """How long the need has been visible: oldest primary signal by post date or first sighting."""
    best = 0
    for sig in stats["primary"]:
        age = sig.age_days(today) or 0
        seen = max(0, (today - sig.first_seen).days) if sig.first_seen else 0
        best = max(best, age, seen)
    return best


def _intent(company: Company, ctx: Any) -> Tuple[float, List[str]]:
    pb = ctx.playbook
    cfg = pb.scoring
    weight = _weights(cfg)["intent"]
    stats = signal_stats(company, ctx)
    if not stats["primary"]:
        return 0.0, ["no primary signal"]
    raw = cfg.get("intent") or {}
    sub = {k: _num(raw.get(k, d), float(d)) for k, d in _DEFAULT_INTENT.items()}
    total_sub = sum(sub.values())
    reasons: List[str] = []

    age = stats["freshest_age"]
    bands = _bands(cfg)
    credit = {
        "fresh": freshness_credit(age, bands),
        "volume": volume_credit(stats["volume"], cfg.get("volume_full_at", 3)),
        "persistence": 1.0 if stats["persistent"] else 0.0,
        "urgency": 1.0 if stats["urgent"] else 0.0,
    }
    if age is None:
        reasons.append("signal date unknown")
    elif credit["fresh"] >= 0.75:
        reasons.append(f"fresh signal ({age}d)")
    elif credit["fresh"] > 0:
        reasons.append(f"signal {age}d old")
    else:
        reasons.append(f"old signal ({age}d)")
    if stats["volume"] > 1:
        reasons.append(f"{stats['volume']} matching signals")
    if stats["persistent"]:
        stale = pb.signals.get("stale_after_days")
        stale_n = int(_num(stale, 21.0))
        reposted = any(s.reposted for s in stats["primary"])
        days = _open_days(stats, ctx.today)
        parts = ["reposted"] if reposted else []
        if days >= (min(7, stale_n) if reposted else stale_n):
            parts.append(f"open {days}d")
        reasons.append(" / ".join(parts) or "persistent need")
    if stats["urgent"]:
        reasons.append("urgent language")

    if total_sub <= 0 or weight <= 0:
        return 0.0, reasons
    earned = sum(sub[k] * credit[k] for k in sub)
    return earned / total_sub * weight, reasons


def _fit(company: Company, ctx: Any) -> Tuple[float, List[str]]:
    cfg = ctx.playbook.scoring
    weight = _weights(cfg)["fit"]
    unknown = min(1.0, _num(cfg.get("unknown_credit", 0.5), 0.5))
    report = fit_report(company, ctx)
    reasons: List[str] = []
    points = 0.0
    for dim, res in report.items():
        if res.status is True:
            points += 1.0
            if res.configured:
                reasons.append(f"{dim} match")
        elif res.status is False:
            reasons.append(f"{dim} mismatch")
        else:
            points += unknown
            reasons.append(f"{dim} unknown")
    return (points / len(report)) * weight if report else 0.0, reasons


_EMAIL_CREDIT = {
    EmailStatus.VALID: (1.0, "email valid"),
    EmailStatus.RISKY: (0.5, "email risky (catch-all)"),
    EmailStatus.UNKNOWN: (0.3, "email unverified"),
    EmailStatus.INVALID: (0.0, "email invalid"),
}


def person_credit(contact: Optional[Contact], ctx: Any) -> Tuple[float, str]:
    """Share of the person half of reachability, plus a reason."""
    if contact is None:
        return 0.0, "no decision-maker yet"
    title = (contact.title or "").strip()
    label = title or contact.full_name or "contact"
    if title and is_excluded_title(title, ctx):
        return 0.0, f"{title} (excluded title)"
    if not as_str_list(ctx.playbook.buyers.get("titles")):
        return 0.8, f"{label} found"
    rank = title_rank(title, ctx)
    if rank is None:
        return 0.3, f"{title} not a target title" if title else "title unknown"
    credit = 1.0 if rank == 0 else 0.8 if rank <= 2 else 0.6
    return credit, f"{title} = buyer #{rank + 1}"


def email_credit(contact: Optional[Contact]) -> Tuple[float, str]:
    """Share of the email half of reachability, plus a reason."""
    if contact is None:
        return 0.0, ""
    if not contact.email:
        return 0.0, "no email"
    return _EMAIL_CREDIT.get(contact.email_status, _EMAIL_CREDIT[EmailStatus.UNKNOWN])


def _reachability(contact: Optional[Contact], ctx: Any) -> Tuple[float, List[str]]:
    weight = _weights(ctx.playbook.scoring)["reachability"]
    person, why_person = person_credit(contact, ctx)
    email, why_email = email_credit(contact)
    reasons = [r for r in (why_person, why_email) if r]
    return (0.5 * person + 0.5 * email) * weight, reasons


def _extra(company: Company, ctx: Any) -> Tuple[float, List[str]]:
    cfg = ctx.playbook.scoring
    weight = _weights(cfg)["extra"]
    per = _num(cfg.get("extra_per_signal", 5), 5.0)
    types = sorted(signal_stats(company, ctx)["secondary_types"])
    reasons = ["+" + t.replace("_", " ") for t in types]
    return min(weight, per * len(types)), reasons


def score(company: Company, contact: Optional[Contact], ctx: Any) -> ScoreBreakdown:
    """Score a company (and optionally the contact we would email) from 0 to 100."""
    intent, r_intent = _intent(company, ctx)
    fit, r_fit = _fit(company, ctx)
    reach, r_reach = _reachability(contact, ctx)
    extra, r_extra = _extra(company, ctx)
    return ScoreBreakdown(
        intent=round(intent, 2),
        fit=round(fit, 2),
        reachability=round(reach, 2),
        extra=round(extra, 2),
        reasons=r_intent + r_fit + r_reach + r_extra,
    )


def tier_for(total: float, ctx: Any) -> str:
    """'hot' if total >= tiers.hot, 'normal' if >= tiers.normal, else 'skip'."""
    tiers = ctx.playbook.scoring.get("tiers") or {}
    hot = _num(tiers.get("hot", 80), 80.0)
    normal = _num(tiers.get("normal", 60), 60.0)
    if total >= hot:
        return Tier.HOT
    if total >= normal:
        return Tier.NORMAL
    return Tier.SKIP


__all__ = ["email_credit", "freshness_credit", "person_credit", "score", "tier_for", "volume_credit"]
