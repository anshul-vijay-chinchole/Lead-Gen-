"""The optional "Suggested opening line" column.

A short first line the CLIENT (the recruiter) can adapt when they approach the
hiring company about the role - we never send anything ourselves.

Two ways to write it:

* ``template_line(row)`` - free, instant, no AI. Built only from facts in the
  row (company, role, location, when it was posted), e.g.
  ``"Saw Acme is hiring a Senior Accountant in Austin, TX (posted 2 days ago)."``
  Missing pieces are simply left out (no dangling "in ," or "()").
* AI - ``add_opening_lines`` with ``opening_line.ai: true``: one short call per
  row to the playbook's writer LLM (``ctx.llm``: ``writer.provider`` /
  ``writer.model``). The model only sees company-level facts from the row (no
  personal data), is told never to invent facts, and its answer is cleaned
  (quotes, emoji, line breaks, labels removed; length capped). An answer that
  mentions a number that is not in the facts, contains a link / email /
  placeholder, or is empty is rejected and the template line is used instead.

Cost cap: before every AI call the worst case is estimated - prompt
characters / 4 input tokens + ``max_tokens`` output tokens at
``ctx.usage.price_for(model)`` - and the call is skipped (template line, and
AI switched off for the rest of the run, ``capped=True``) if it could push
the run's AI spend over ``max_cost_usd``. After a call its real cost is taken
from ``llm.last_usage`` when the client reports token usage (a dict or object
with ``input_tokens`` / ``output_tokens`` or ``prompt_tokens`` /
``completion_tokens``); otherwise the worst-case estimate is charged, so the
cap errs on the safe side. Token usage is recorded in ``ctx.usage`` (unless
the LLM client already recorded it itself) so it shows up in the run's usage
and cost summary.

Never raises: an LLM error, a missing key or the paid-lookup budget running
out gives the template line for that row. After ``writer.max_llm_failures``
(default 3) failures in a row, or a permanent error (bad key / config), AI is
switched off for the rest of the run. In ``ctx.dry_run`` the AI is never
called.

Client settings read (``client.opening_line``, a mapping or an object):

enabled       Whether the column exists at all - checked by the caller
              (``run.py``); this module fills the lines when it is called.
ai            true = AI lines (default false = template lines).
max_cost_usd  Per-run cap on the estimated AI spend in USD (default 0.50;
              0 = no AI spend at all, i.e. template lines only). A value
              that is not a finite number (NaN, infinity, text) never means
              "no cap": the default cap is used and a QA note says so.

The output budget per AI call is 400 tokens (``DEFAULT_MAX_TOKENS``). It is not
a client-file setting (client files reject unknown keys); only code that
builds ``client`` itself (a mapping or object) can set ``opening_line.max_tokens``.

Where the role is: the row's Location column is company-level (the HQ for
TheirStack, every job location joined for ATS boards), so "hiring ... in
<place>" uses the job's own location - ``row["_job_location"]``, which
``add_opening_lines`` fills from the lead's top signal - and leaves the place
out when the job's location is unknown. It never states the HQ as the job's
location.

Playbook keys read: ``writer.max_llm_failures`` (and, through ``ctx.llm``, the
``writer`` provider / model settings); ``usage.llm_price_per_mtok`` through
``ctx.usage.price_for``.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Sequence, Tuple

from ..context import MissingCredentialError
from ..models import SignalType
from ..usage import BudgetExceeded, UsageMeter

DEFAULT_MAX_COST_USD = 0.50
DEFAULT_MAX_TOKENS = 400
DEFAULT_MAX_FAILURES = 3
MAX_WORDS = 35             # hard cap on an AI line (the prompt asks for ~30)
MAX_CHARS = 240
MAX_TITLE_CHARS = 80
MAX_LOCATION_CHARS = 60
MAX_COMPANY_CHARS = 80
MAX_EXCERPT_CHARS = 400

#: Internal row key: where the job in the line is (set by ``add_opening_lines``).
JOB_LOCATION_KEY = "_job_location"

REMOTE_WORDS = ("remote", "anywhere", "worldwide", "work from home", "wfh")

# How a non-hiring signal reads in "Saw the recent <phrase> at Acme".
SIGNAL_PHRASES: Dict[str, str] = {
    "funding": "funding news",
    "leadership change": "leadership change",
    "expansion": "expansion news",
    "headcount growth": "headcount growth",
    "tech adoption": "tech news",
    "reviews": "reviews",
    "ad activity": "ad activity",
    "website change": "website update",
    "event": "event news",
}

SYSTEM_PROMPT = (
    "You help recruitment agencies start conversations with companies that are hiring. "
    "Write ONE opening line that a recruiter could use when first contacting the company "
    "described below about the role it is hiring for. The recruiter sends it, not you.\n"
    "Rules:\n"
    "- One sentence, at most 30 words, friendly, specific and natural.\n"
    "- Use only the facts given. Never invent numbers, names, dates, reasons, company news, "
    "growth, funding or candidates.\n"
    "- Mention the role (and the location or timing if given) so it is clearly about this company.\n"
    "- No greeting, no sign-off, no sales pitch, no mention of fees.\n"
    "- Plain text only: no quotation marks, no emoji, no hashtags, no links, no placeholders "
    "like [Name].\n"
    "Reply with the line only."
)


@dataclass
class OpeningStats:
    """What ``add_opening_lines`` did (read by the QA report)."""

    ai_lines: int = 0
    template_lines: int = 0
    cost_usd: float = 0.0          # estimated AI spend of this run (USD)
    capped: bool = False           # True when the cost cap stopped the AI
    errors: int = 0                # AI calls that failed or gave an unusable answer
    notes: List[str] = field(default_factory=list)   # plain-English explanations for the QA summary


# --- helpers ----------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")
_MORE_RE = re.compile(r"\s*\(\+(\d+) more\)\s*$")


def _clean(value: Any, limit: int = 0) -> str:
    """One-line text: whitespace collapsed, wrapping quotes / trailing punctuation removed."""
    text = _WS_RE.sub(" ", "" if value is None else str(value)).strip()
    text = text.strip("\"'`“”‘’").strip()
    text = text.rstrip(" ,;:-").strip()
    if limit and len(text) > limit:
        cut = text[:limit].rsplit(" ", 1)[0] if " " in text[:limit] else text[:limit]
        text = cut.rstrip(" ,;:-")
    return text


def _titles(value: Any) -> Tuple[List[str], int]:
    """(``job_titles`` split into titles, total number of roles incl. "(+N more)")."""
    text = "" if value is None else str(value)
    more = 0
    m = _MORE_RE.search(text)
    if m:
        more = int(m.group(1))
        text = text[:m.start()]
    titles = [t for t in (_clean(x, MAX_TITLE_CHARS).rstrip(".") for x in text.split(";")) if t]
    return titles, len(titles) + more


def _article(phrase: str) -> str:
    """'a' or 'an' for the phrase that follows (good enough for job titles)."""
    word = phrase.split(" ", 1)[0].strip("(\"'") if phrase else ""
    if not word:
        return "a"
    lower = word.lower()
    if word[0].isdigit():
        return "an" if lower.startswith("8") or re.match(r"^(11|18)(\D|$)", lower) else "a"
    if len(word) > 1 and word.isupper():               # acronyms: "an HR ...", "a UX ..."
        return "an" if word[0] in "AEFHILMNORSX" else "a"
    if lower.startswith(("uni", "use", "usu", "uti", "eu", "one", "once")):
        return "a"
    if lower.startswith(("hour", "honest", "honor", "honour", "heir")):
        return "an"
    return "an" if lower[0] in "aeiou" else "a"


def _is_remote(location: str) -> bool:
    low = location.lower()
    return any(low == w or low.startswith(w) for w in REMOTE_WORDS)


def _when(row: Mapping[str, Any]) -> str:
    """'posted 2 days ago' / 'posted today' / 'first seen 3 days ago' / 'posted on 2026-09-20' / ''."""
    posted = _clean(row.get("posted"))
    low = posted.lower()
    if low.startswith("posted "):
        return posted
    m = re.search(r"first seen ([^)]+)", posted)
    if m:
        return "first seen " + m.group(1).strip()
    day = _clean(row.get("date_posted"))
    if day and not low.startswith("date unknown"):
        return f"posted on {day[:10]}"
    return ""


def _finish(line: str) -> str:
    line = _WS_RE.sub(" ", line).strip()
    return line if line.endswith((".", "!", "?")) else line + "."


def _top_signal(lead: Any) -> Any:
    """The signal the row describes (same pick as ``rows.build_row``: first job posting, else first)."""
    sigs = list(getattr(getattr(lead, "company", None), "signals", None) or [])
    jobs = [s for s in sigs if getattr(s, "type", "") == SignalType.JOB_POSTING]
    pick = jobs or sigs
    return pick[0] if pick else None


def _role_location(row: Mapping[str, Any], lead: Any = None) -> str:
    """Where the role the line talks about is.

    The row's "Location" column is company-level (the HQ for TheirStack, every job location
    joined for ATS boards), so a hiring line uses the job's own location: ``row["_job_location"]``
    (set by ``add_opening_lines``) > the lead's top signal > the row's Location (a plain row
    with neither, e.g. from an old caller). A known lead whose job has no location -> ''.
    """
    if JOB_LOCATION_KEY in row:
        return _clean(row.get(JOB_LOCATION_KEY), MAX_LOCATION_CHARS)
    if lead is not None and getattr(lead, "company", None) is not None:
        top = _top_signal(lead)
        return _clean(getattr(top, "location", "") if top is not None else "", MAX_LOCATION_CHARS)
    return _clean(row.get("location"), MAX_LOCATION_CHARS)


# --- template ---------------------------------------------------------------------------

def template_line(row: Mapping[str, Any]) -> str:
    """A free, factual opening line built only from the row (no AI).

    "hiring ... in <place>" uses the job's location (``row["_job_location"]``, see
    ``_role_location``), never the company's HQ; other lines use the row's Location.
    """
    company = _clean(row.get("company"), MAX_COMPANY_CHARS)
    signal = _clean(row.get("signal_type"))
    location = _clean(row.get("location"), MAX_LOCATION_CHARS)
    titles, total = _titles(row.get("job_titles"))
    when = _when(row)
    tail = f" ({when})" if when else ""

    if signal.lower() == "hiring":
        location = _role_location(row)
        remote = bool(location) and _is_remote(location)
        subject = f"Saw {company} is hiring" if company else "Saw you are hiring"
        adj = "remote " if remote else ""
        if titles and total == 1:
            role = f"{adj}{titles[0]}"
            what = f" {_article(role)} {role}"
        elif titles:
            what = f" for {total} {adj}roles, including {_article(titles[0])} {titles[0]}"
        elif total > 1:
            what = f" for {total} {adj}roles"
        else:
            what = " remotely" if remote else ""
        where = f" in {location}" if location and not remote else ""
        return _finish(f"{subject}{what}{where}{tail}")

    if signal:
        phrase = SIGNAL_PHRASES.get(signal.lower(), "news")
        at = f" at {company}" if company else ""
        detail = f": {titles[0]}" if titles else ""
        return _finish(f"Saw the recent {phrase}{at}{detail}{tail}")

    if company:
        where = f" in {location}" if location else ""
        return _finish(f"Came across {company}{where}")
    return _finish("Came across your company" + (f" in {location}" if location else ""))


# --- AI ---------------------------------------------------------------------------------

def _lead_facts(lead: Any) -> Tuple[str, str]:
    """(job ad excerpt, company description excerpt) from the Lead, when available."""
    company = getattr(lead, "company", None)
    if company is None:
        return "", ""
    job = ""
    for s in getattr(company, "signals", None) or []:
        if getattr(s, "type", "") == "job_posting" and getattr(s, "description", ""):
            job = _clean(s.description, MAX_EXCERPT_CHARS)
            break
    about = _clean(getattr(company, "description", ""), 300)
    return job, about


def build_prompt(row: Mapping[str, Any], lead: Any = None) -> Tuple[str, str]:
    """(system, user) prompts for one row. Only company-level facts; no personal data."""
    facts: List[Tuple[str, str]] = []
    titles, total = _titles(row.get("job_titles"))
    signal = _clean(row.get("signal_type"))
    facts.append(("Company", _clean(row.get("company"), MAX_COMPANY_CHARS)))
    if signal.lower() == "hiring":
        facts.append(("Hiring for", "; ".join(titles)))
        if total > len(titles):
            facts.append(("Number of matching open roles", str(total)))
    else:
        facts.append(("Signal", signal))
        facts.append(("Details", "; ".join(titles)))
    # hiring: where the job is (never the company's HQ); other signals: where the company is
    facts.append(("Location", _role_location(row, lead) if signal.lower() == "hiring"
                  else _clean(row.get("location"), MAX_LOCATION_CHARS)))
    when = _when(row)
    facts.append(("Posted", when))
    facts.append(("Industry", _clean(row.get("industry"))))
    size = row.get("company_size")
    if isinstance(size, int) and not isinstance(size, bool) and size > 0:
        facts.append(("Company size", f"about {size} employees"))
    job, about = _lead_facts(lead)
    facts.append(("From the job ad", job))
    facts.append(("About the company", about))
    lines = [f"- {k}: {v}" for k, v in facts if v]
    user = "Facts:\n" + "\n".join(lines) + "\n\nWrite the opening line."
    return SYSTEM_PROMPT, user


_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF\U0000FE0F\U0000200D\U000020E3]")
_LABEL_RE = re.compile(r"^(?:suggested\s+)?(?:opening\s+line|opener|line)\s*[:\-]\s*", re.I)
_BULLET_RE = re.compile(r"^(?:[-*•]\s+|\d+[.)]\s+)")
_BAD_RE = re.compile(r"(\[[^\]]*\]|\{[^}]*\}|<[^>]*>|https?://|www\.|\S+@\S+\.\S+|#\w)", re.I)
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
_WRAP_QUOTES = "\"'`“”‘’«»"
_INNER_QUOTES = "\"“”«»`"


def clean_ai_line(text: Any, facts: str = "") -> str:
    """Clean a model answer into one plain line; '' when it is unusable.

    Picks the first real line (skipping "Here is ...:" preambles), removes labels,
    bullets, quotes and emoji, collapses whitespace and caps the length at
    ``MAX_WORDS`` / ``MAX_CHARS``. With ``facts``, a number the facts do not
    contain makes the answer unusable (the model must not invent figures).
    """
    raw = "" if text is None else str(text)
    raw = raw.replace("```", "\n")
    candidates = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    candidates = [ln for ln in candidates if not ln.rstrip().endswith(":")] or candidates
    if not candidates:
        return ""
    line = candidates[0]
    line = _BULLET_RE.sub("", line)
    line = _LABEL_RE.sub("", line)
    line = _EMOJI_RE.sub("", line)
    line = line.replace("’", "'").strip()
    while line and (line[0] in _WRAP_QUOTES or line[-1] in _WRAP_QUOTES):
        line = line.strip(_WRAP_QUOTES).strip()
    for q in _INNER_QUOTES:
        line = line.replace(q, "")
    line = line.replace("*", "").replace("_", " ")
    line = _WS_RE.sub(" ", line).strip()
    if not line or _BAD_RE.search(line):
        return ""
    if facts:
        known = set(_NUMBER_RE.findall(facts))
        if any(n not in known for n in _NUMBER_RE.findall(line)):
            return ""
    words = line.split(" ")
    if len(words) < 4:
        return ""
    if len(words) > MAX_WORDS or len(line) > MAX_CHARS:
        # keep whole sentences while they fit; else cut at a word boundary
        sentences = re.split(r"(?<=[.!?])\s+", line)
        kept = ""
        for s in sentences:
            nxt = f"{kept} {s}".strip()
            if len(nxt.split(" ")) > MAX_WORDS or len(nxt) > MAX_CHARS:
                break
            kept = nxt
        if not kept:
            kept = " ".join(words[:MAX_WORDS])
            while len(kept) > MAX_CHARS and " " in kept:
                kept = kept.rsplit(" ", 1)[0]
            kept = kept.rstrip(" ,;:-")
        line = kept
    return _finish(line)


def worst_case_cost(system: str, user: str, model: str, max_tokens: int, meter: Any = None) -> float:
    """Upper estimate (USD) of one call: prompt chars / 4 input tokens + ``max_tokens`` output."""
    meter = meter if meter is not None else UsageMeter()
    input_tokens = int(math.ceil((len(system) + len(user)) / 4))
    return meter.llm_cost(model, input_tokens, max(0, int(max_tokens)))


def _usage_tokens(usage: Any) -> Optional[Tuple[int, int]]:
    """(input, output) tokens from an ``llm.last_usage`` dict / object, or None."""
    if usage is None:
        return None

    def get(key: str) -> Any:
        return usage.get(key) if isinstance(usage, Mapping) else getattr(usage, key, None)

    def num(*keys: str) -> Optional[int]:
        for k in keys:
            v = get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return max(0, int(v))
        return None

    inp = num("input_tokens", "prompt_tokens")
    out = num("output_tokens", "completion_tokens")
    if inp is None and out is None:
        return None
    extra = sum(num(k) or 0 for k in ("cache_creation_input_tokens", "cache_read_input_tokens"))
    return (inp or 0) + extra, out or 0


def _meter_totals(meter: Any) -> Tuple[int, float]:
    adapters = getattr(meter, "adapters", None)
    if not isinstance(adapters, Mapping):
        return 0, 0.0
    tokens = sum(int(getattr(u, "input_tokens", 0) or 0) + int(getattr(u, "output_tokens", 0) or 0)
                 for u in adapters.values())
    cost = sum(float(getattr(u, "llm_cost", 0.0) or 0.0) for u in adapters.values())
    return tokens, cost


def _setting(client: Any, key: str, default: Any) -> Any:
    section = client.get("opening_line") if isinstance(client, Mapping) else getattr(client, "opening_line", None)
    if isinstance(section, Mapping):
        value = section.get(key)
    else:
        value = getattr(section, key, None)
    return default if value is None else value


def _finite(value: Any) -> Optional[float]:
    """``value`` as a finite float, else None (NaN / infinity would switch the cost cap off)."""
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return out if math.isfinite(out) else None


def _as_float(value: Any, default: float) -> float:
    out = _finite(value)
    return default if out is None else out


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _is_permanent(exc: BaseException) -> bool:
    return isinstance(exc, MissingCredentialError) or bool(getattr(exc, "permanent", False))


def _set_template(row: MutableMapping[str, Any], stats: OpeningStats) -> None:
    row["opening_line"] = template_line(row)
    row["_opening_source"] = "template"
    stats.template_lines += 1


def _resolve_llm(ctx: Any, max_cost: float, stats: OpeningStats, log: logging.Logger) -> Any:
    """The LLM client to use for AI lines, or None (with a note in ``stats`` saying why)."""
    if getattr(ctx, "dry_run", False):
        stats.notes.append("dry run: template opening lines (the AI is never called in a dry run)")
        return None
    if max_cost <= 0:
        stats.capped = True
        stats.notes.append("opening_line.max_cost_usd is 0, so no AI spend is allowed - template lines used")
        return None
    try:
        llm = ctx.llm
    except Exception as e:  # noqa: BLE001 - a broken LLM config must not stop the delivery
        log.warning("opening lines: AI unavailable (%s) - using template lines", e)
        stats.notes.append(f"AI opening lines unavailable ({e}) - template lines used")
        return None
    if llm is None:
        stats.notes.append("AI opening lines are on but no AI provider is configured (set "
                           "writer.provider / writer.model and the API key) - template lines used")
    return llm


class _CallCost:
    """Works out what one LLM call cost. Create it right before the call, ``settle()`` after.

    Order of preference: the client recorded the tokens in ``ctx.usage`` itself (use the
    meter's difference, record nothing) > ``llm.last_usage`` from this call (record it) >
    the worst-case estimate (record it). A call that failed without reporting usage is
    free (HTTP errors are not billed), except a truncated answer, which used the whole
    output budget.
    """

    def __init__(self, llm: Any, meter: Any) -> None:
        self.llm, self.meter = llm, meter
        self.prev_usage = getattr(llm, "last_usage", None)
        self.reset = False
        if hasattr(llm, "last_usage"):
            try:
                llm.last_usage = None          # so a stale value is never mistaken for this call's
                self.reset = True
            except Exception:  # noqa: BLE001 - read-only attribute: compare identities instead
                self.reset = False
        self.tokens_before, self.cost_before = _meter_totals(meter)

    def settle(self, worst_tokens: Tuple[int, int], failure: Optional[BaseException],
               pricing: Any, usage_type: str, model: str) -> float:
        tokens_after, cost_after = _meter_totals(self.meter)
        if tokens_after > self.tokens_before:
            return max(0.0, cost_after - self.cost_before)
        usage = getattr(self.llm, "last_usage", None)
        fresh = usage is not None and (self.reset or usage is not self.prev_usage)
        tokens = _usage_tokens(usage) if fresh else None
        if tokens is None and (failure is None or getattr(failure, "truncated", False)):
            tokens = worst_tokens
        if tokens is None:
            return 0.0
        if self.meter is not None and hasattr(self.meter, "record_llm"):
            return float(self.meter.record_llm("llm", usage_type, model, tokens[0], tokens[1]))
        return float(pricing.llm_cost(model, tokens[0], tokens[1]))


def add_opening_lines(rows: Sequence[MutableMapping[str, Any]], leads_by_id: Optional[Mapping[str, Any]],
                      ctx: Any, client: Any) -> OpeningStats:
    """Fill ``row["opening_line"]`` for every row (template, or AI within the cost cap). Never raises.

    ``leads_by_id`` maps ``row["_lead_id"]`` to its ``Lead`` (optional; gives the job's own
    location and adds the job-ad excerpt to the AI prompt). Each row also gets
    ``row["_opening_source"]`` = "ai" or "template" and, when its lead is known,
    ``row["_job_location"]`` (both internal; never written to client files).
    """
    stats = OpeningStats()
    log: logging.Logger = getattr(ctx, "log", None) or logging.getLogger("leadgen")
    leads_by_id = leads_by_id or {}
    want_ai = _as_bool(_setting(client, "ai", False))
    raw_cap = _setting(client, "max_cost_usd", DEFAULT_MAX_COST_USD)
    max_cost = _finite(raw_cap)
    if max_cost is None:     # NaN / infinity / text: never "no cap" - the default cap applies
        max_cost = DEFAULT_MAX_COST_USD
        if want_ai:
            stats.notes.append(f"opening_line.max_cost_usd {raw_cap!r} is not a usable amount - the default AI "
                               f"spending limit of ${DEFAULT_MAX_COST_USD:.2f} was used")
    max_tokens = max(50, int(_as_float(_setting(client, "max_tokens", DEFAULT_MAX_TOKENS), DEFAULT_MAX_TOKENS)))

    for row in rows:         # where each row's job is (the Location column is company-level)
        lead = leads_by_id.get(str(row.get("_lead_id") or ""))
        if lead is not None and getattr(lead, "company", None) is not None and JOB_LOCATION_KEY not in row:
            top = _top_signal(lead)
            row[JOB_LOCATION_KEY] = str(getattr(top, "location", "") or "") if top is not None else ""

    llm = _resolve_llm(ctx, max_cost, stats, log) if (want_ai and rows) else None
    writer_cfg = getattr(getattr(ctx, "playbook", None), "writer", None) or {}
    max_failures = max(1, int(_as_float(writer_cfg.get("max_llm_failures"), DEFAULT_MAX_FAILURES)
                              or DEFAULT_MAX_FAILURES))
    meter = getattr(ctx, "usage", None)
    pricing = meter if meter is not None else UsageMeter()
    model = str(getattr(llm, "model", "") or "")
    usage_type = str(getattr(llm, "type_name", "") or getattr(llm, "name", "") or "llm")
    in_a_row = 0

    for row in rows:
        if llm is None:
            _set_template(row, stats)
            continue
        system, user = build_prompt(row, leads_by_id.get(str(row.get("_lead_id") or "")))

        # 1. cost cap: skip the call if its worst case could take the spend over the cap
        worst = worst_case_cost(system, user, model, max_tokens, pricing)
        if stats.cost_usd + worst > max_cost + 1e-12:
            stats.capped = True
            stats.notes.append(f"AI cost cap of ${max_cost:.2f} reached after {stats.ai_lines} AI line(s) - "
                               "template lines used for the rest")
            log.info("opening lines: cost cap $%.2f reached (spent ~$%.4f) - template lines for the rest",
                     max_cost, stats.cost_usd)
            llm = None
            _set_template(row, stats)
            continue

        # 2. the call (never raises out of here)
        probe = _CallCost(llm, meter)
        answer: Optional[str] = None
        failure: Optional[BaseException] = None
        try:
            answer = llm.complete(system, user, max_tokens=max_tokens)
        except BudgetExceeded as e:        # refused before any network call: nothing spent
            stats.notes.append(f"paid-lookup budget reached ({e}) - template lines used for the rest")
            log.info("opening lines: %s - template lines for the rest", e)
            llm = None
            _set_template(row, stats)
            continue
        except Exception as e:  # noqa: BLE001 - never raise: fall back to the template
            failure = e

        # 3. what it cost
        worst_tokens = (int(math.ceil((len(system) + len(user)) / 4)), max_tokens)
        cost = probe.settle(worst_tokens, failure, pricing, usage_type, model)
        stats.cost_usd = round(stats.cost_usd + cost, 6)

        # 4. use the answer, or fall back to the template line
        line = clean_ai_line(answer, user) if failure is None else ""
        if failure is None and not line:
            failure = ValueError(f"unusable answer: {str(answer)[:80]!r}")
        if failure is not None:
            stats.errors += 1
            in_a_row += 1
            log.warning("opening lines: AI line failed for %s (%s) - template line used",
                        row.get("company") or "a lead", failure)
            _set_template(row, stats)
            if _is_permanent(failure) or in_a_row >= max_failures:
                why = "a configuration / key problem" if _is_permanent(failure) else f"{in_a_row} failures in a row"
                stats.notes.append(f"AI opening lines switched off after {why} ({failure}) - template lines used")
                llm = None
            continue

        in_a_row = 0
        row["opening_line"] = line
        row["_opening_source"] = "ai"
        stats.ai_lines += 1

    if stats.ai_lines or stats.errors:
        log.info("opening lines: %d AI, %d template, %d AI error(s), ~$%.4f", stats.ai_lines,
                 stats.template_lines, stats.errors, stats.cost_usd)
    return stats


__all__ = ["OpeningStats", "SYSTEM_PROMPT", "add_opening_lines", "build_prompt", "clean_ai_line",
           "template_line", "worst_case_cost"]
