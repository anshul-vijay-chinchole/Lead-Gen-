"""API usage metering + the per-run paid-lookup budget.

Every adapter reaches the network through ``Adapter.http``, which wraps
``ctx.http`` in a ``MeteredHttp``. Each request is counted per adapter; a
request made by a *paid* adapter (see ``registry.PAID``) is a "paid lookup".
When ``UsageMeter.max_paid_lookups`` (the ``--budget`` flag, or
``usage.max_paid_lookups``) is reached, further paid requests raise
``BudgetExceeded`` *before* touching the network - free adapters keep working.

Cost estimates:
  * paid requests x ``usage.cost_per_call[<adapter type>]`` (USD, you set it
    from your plan - providers price per plan, so there is no built-in number);
  * AI tokens x a price per million tokens (``usage.llm_price_per_mtok`` or the
    built-in table in ``LLM_PRICES``; unknown models use a deliberately high
    fallback so cost caps err on the safe side).
Providers that report remaining credits (e.g. MillionVerifier) are shown too.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


class BudgetExceeded(RuntimeError):
    """The per-run paid-lookup budget is used up. Raised before any network call."""


# USD per million tokens (input, output). Used only for estimates / cost caps.
# Check your provider's current pricing and override with usage.llm_price_per_mtok.
# (Anthropic first-party API list prices, cached 2026-06.) OpenAI / other models are
# not listed: set their price in usage.llm_price_per_mtok, or the fallback applies.
LLM_PRICES: Dict[str, Tuple[float, float]] = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
FALLBACK_LLM_PRICE: Tuple[float, float] = (10.0, 50.0)  # unknown model: assume top-tier price


@dataclass
class AdapterUsage:
    kind: str
    type: str
    paid: bool = False
    calls: int = 0
    blocked: int = 0                      # requests refused by the budget
    credits_remaining: Optional[float] = None
    input_tokens: int = 0
    output_tokens: int = 0
    llm_cost: float = 0.0


@dataclass
class UsageMeter:
    max_paid_lookups: int = 0             # 0 = no cap
    cost_per_call: Dict[str, float] = field(default_factory=dict)
    llm_prices: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    adapters: Dict[Tuple[str, str], AdapterUsage] = field(default_factory=dict)

    @classmethod
    def from_playbook(cls, pb: Any, budget: Optional[int] = None) -> "UsageMeter":
        cfg = getattr(pb, "usage", None) or {}
        prices: Dict[str, Tuple[float, float]] = {}
        for model, p in (cfg.get("llm_price_per_mtok") or {}).items():
            try:
                prices[str(model)] = (float(p.get("input")), float(p.get("output")))
            except (AttributeError, TypeError, ValueError):
                continue
        costs: Dict[str, float] = {}
        for k, v in (cfg.get("cost_per_call") or {}).items():
            try:
                costs[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
        cap = budget if budget is not None else int(cfg.get("max_paid_lookups") or 0)
        return cls(max_paid_lookups=max(0, int(cap or 0)), cost_per_call=costs, llm_prices=prices)

    # --- counting ---------------------------------------------------------------
    def _get(self, kind: str, type_: str, paid: bool) -> AdapterUsage:
        key = (kind or "?", type_ or "?")
        u = self.adapters.get(key)
        if u is None:
            u = self.adapters[key] = AdapterUsage(kind=key[0], type=key[1], paid=paid)
        u.paid = u.paid or paid
        return u

    @property
    def paid_lookups(self) -> int:
        return sum(u.calls for u in self.adapters.values() if u.paid)

    @property
    def remaining(self) -> Optional[int]:
        if not self.max_paid_lookups:
            return None
        return max(0, self.max_paid_lookups - self.paid_lookups)

    @property
    def exhausted(self) -> bool:
        return bool(self.max_paid_lookups) and self.paid_lookups >= self.max_paid_lookups

    def before_request(self, kind: str, type_: str, paid: bool) -> None:
        """Count one request; raise BudgetExceeded (without counting) if a paid one is over budget."""
        u = self._get(kind, type_, paid)
        if paid and self.exhausted:
            u.blocked += 1
            raise BudgetExceeded(f"paid-lookup budget of {self.max_paid_lookups} reached "
                                 f"({type_} request skipped)")
        u.calls += 1

    def note_credits(self, kind: str, type_: str, remaining: Any) -> None:
        try:
            self._get(kind, type_, True).credits_remaining = float(remaining)
        except (TypeError, ValueError):
            pass

    def price_for(self, model: str) -> Tuple[float, float]:
        m = str(model or "")
        if m in self.llm_prices:
            return self.llm_prices[m]
        for known, price in LLM_PRICES.items():
            if m == known or m.startswith(known + "-"):
                return price
        return FALLBACK_LLM_PRICE

    def llm_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        pin, pout = self.price_for(model)
        return (max(0, input_tokens) * pin + max(0, output_tokens) * pout) / 1_000_000

    def record_llm(self, kind: str, type_: str, model: str, input_tokens: int, output_tokens: int) -> float:
        u = self._get(kind, type_, True)
        u.input_tokens += max(0, int(input_tokens or 0))
        u.output_tokens += max(0, int(output_tokens or 0))
        cost = self.llm_cost(model, int(input_tokens or 0), int(output_tokens or 0))
        u.llm_cost += cost
        return cost

    # --- reporting ----------------------------------------------------------------
    @property
    def estimated_cost(self) -> Optional[float]:
        """USD estimate for everything we can price (None if nothing is priceable)."""
        total, any_priced = 0.0, False
        for u in self.adapters.values():
            if u.llm_cost:
                total += u.llm_cost
                any_priced = True
            if u.paid and u.type in self.cost_per_call and not (u.input_tokens or u.output_tokens):
                total += u.calls * self.cost_per_call[u.type]
                any_priced = True
        return round(total, 4) if any_priced else None

    def rows(self) -> List[Dict[str, Any]]:
        out = []
        for u in sorted(self.adapters.values(), key=lambda x: (not x.paid, x.kind, x.type)):
            est: Optional[float] = None
            if u.input_tokens or u.output_tokens:
                est = round(u.llm_cost, 4)
            elif u.paid and u.type in self.cost_per_call:
                est = round(u.calls * self.cost_per_call[u.type], 4)
            out.append({"kind": u.kind, "type": u.type, "paid": u.paid, "calls": u.calls,
                        "blocked_by_budget": u.blocked, "credits_remaining": u.credits_remaining,
                        "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                        "estimated_cost_usd": est})
        return out

    def summary_lines(self) -> List[str]:
        rows = self.rows()
        cap = f"{self.paid_lookups}/{self.max_paid_lookups}" if self.max_paid_lookups else f"{self.paid_lookups} (no cap)"
        lines = [f"API usage: paid lookups {cap}"]
        if not rows:
            lines.append("  no API calls (free / offline run)")
        for r in rows:
            bits = [f"{r['calls']} call{'s' if r['calls'] != 1 else ''}"]
            if r["blocked_by_budget"]:
                bits.append(f"{r['blocked_by_budget']} skipped by budget")
            if r["input_tokens"] or r["output_tokens"]:
                bits.append(f"{r['input_tokens']}+{r['output_tokens']} tokens")
            if r["credits_remaining"] is not None:
                bits.append(f"{r['credits_remaining']:g} credits left")
            if r["estimated_cost_usd"] is not None:
                bits.append(f"~${r['estimated_cost_usd']:.4f}")
            tag = "paid" if r["paid"] else "free"
            lines.append(f"  {r['kind']} {r['type']} ({tag}): " + ", ".join(bits))
        est = self.estimated_cost
        if est is not None:
            lines.append(f"  estimated cost: ~${est:.4f}")
        elif self.paid_lookups:
            lines.append("  estimated cost: n/a (set usage.cost_per_call.<type> to your plan's price per request)")
        return lines


class MeteredHttp:
    """Wraps an http client (HttpClient or a test fake); counts + budgets every request."""

    def __init__(self, inner: Any, meter: UsageMeter, kind: str, type_: str, paid: bool):
        self._inner = inner
        self._meter = meter
        self._kind, self._type, self._paid = kind, type_, paid

    def request(self, method: str, url: str, **kw: Any) -> Any:
        self._meter.before_request(self._kind, self._type, self._paid)
        return self._inner.request(method, url, **kw)

    def get(self, url: str, **kw: Any) -> Any:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw: Any) -> Any:
        return self.request("POST", url, **kw)

    def get_json(self, url: str, **kw: Any) -> Any:
        return self.request("GET", url, **kw).json()

    def post_json(self, url: str, **kw: Any) -> Any:
        return self.request("POST", url, **kw).json()

    def __getattr__(self, name: str) -> Any:  # calls, routes, session ... of the wrapped client
        return getattr(self._inner, name)
