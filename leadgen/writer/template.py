"""Template copywriter (``writer.type: template``) - works offline, costs $0.

Turns a lead into a sequence with one message per ``writer.sequence`` step,
using niche-agnostic default copy that is driven by the lead's top signal
(``Lead.top_signal``). It is also the fallback for the AI writer, and this
module hosts the text helpers every writer shares (safe placeholder filling,
tidying, signature/footer handling).

Default sequence (any length works)
-----------------------------------
step 1  opener   - signal-specific opening line + hypothesis + value line + CTA
step 2  bump     - short follow-up with the proof point
step 3  value    - offers something useful (a short plan), booking link if set
last    breakup  - polite close, easy to reply to
A 2-step sequence is opener + breakup, 3 steps is opener + bump + breakup;
steps beyond the four default templates reuse the last follow-up style
(two extra check-in variants, then the last one repeats) and the breakup
always stays last.

Subjects - the thread convention
--------------------------------
Step 1 gets a short, lowercase-ish subject (<= 6 words, e.g.
``senior accountant at Acme``). Follow-up steps get subject ``""``, which
means *reply in the same thread* (``Re: <step 1 subject>``). Exporters rely
on this convention; an explicit non-empty follow-up subject from
``writer.templates`` is kept and means "start a new thread".

Placeholders
------------
Templates use ``{name}`` or ``{{name}}``. Unknown / empty placeholders render
as ``""`` and the result is tidied (double spaces, blank lines, space before
punctuation, dangling words like ``including a .`` are cleaned up).

``first_name`` (fallback ``there``), ``last_name``, ``full_name``, ``contact_title``,
``company`` (display name without legal suffix), ``company_full``, ``domain``,
``industry``, ``employees``, ``location``, ``signal_type``, ``signal_title``
(cleaned: no ``- Hybrid``/``(m/f/d)`` noise), ``signal_title_full``,
``signal_title_lc`` (lowercase-ish), ``a_signal_title`` (with a/an),
``signal_count`` (number of signals of the top signal's type),
``signal_location``, ``signal_url``, ``funding_label`` (e.g. ``$20M Series B``),
``signal_phrase`` (e.g. ``saw you're hiring a Senior Accountant``),
``signal_age_phrase`` (``today`` / ``yesterday`` / ``this week`` / ``recently`` /
``a few weeks ago`` / ``a while back``), ``signal_topic`` (e.g. ``the Senior
Accountant hire``), ``opener`` (= personalization line), ``hypothesis``,
``service``, ``value_prop``, ``proof``, ``cta``, ``booking_link``, ``sender_name``,
``sender_company``, ``sender_title``, ``sender_website``, ``value_line`` /
``proof_line`` / ``booking_line`` / ``bump_line`` (whole sentences, ``""`` when
the offer field is empty), ``signature``, ``footer``, ``step``, ``day``.

Playbook keys read
------------------
``writer.sequence``   steps (``day``) - one message each.
``writer.templates``  optional overrides::

    templates:
      subject: "{signal_title_lc} at {company}"          # step 1 subject
      subjects: ["...", ""]                             # per-step subjects
      steps: ["Hi {first_name}, ...", "..."]            # bodies, or
      steps: [{subject: "...", body: "..."}, ...]
      signal_phrases: {job_posting: "...", default: "..."}   # per signal type
      hypotheses: {funding: "...", default: "..."}
      topics: {expansion: "...", default: "..."}

  With 2+ override steps, extra sequence steps reuse the last override; with
  a single override step only step 1 is replaced. A signal's own
  ``data['phrase']`` / ``data['hypothesis']`` / ``data['topic']`` win over both.
``offer.*``           sender identity, ``service`` (a noun phrase, e.g.
                      "finance recruitment"), ``value_prop`` (completes "We
                      help teams like Acme ...", e.g. "fill finance roles in
                      under 3 weeks" - or a full sentence starting with
                      We/Our or ending in a period), ``proof`` (a sentence),
                      ``cta``, ``booking_link``, ``signature`` (default
                      ``{sender_name}\\n{sender_title}, {sender_company}``),
                      ``footer`` (appended to every email when set).
``signals.stale_after_days``  a job post open this long gets the "still open"
                      hypothesis.

The output is checked with ``leadgen.writer.guardrails``; problems become
``WriterOutput.warnings`` (prefixed ``template:``) so a custom template that
breaks the rules is visible in exports.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from ..models import Lead, Message, Signal, SignalType
from .base import Writer, WriterOutput

# --- text helpers (shared by all writers) -----------------------------------------------------

PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}|\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}")

_DANGLING_WORDS = ("including", "such as", "like", "for", "about", "with", "at", "on", "of", "in",
                   "into", "to", "from", "and", "or", "by")
# A connector word left hanging where an emptied placeholder used to be:
# "roles, including a ." / "expanding into ." / "hiring a ." -> dropped.
_DANGLING_RE = re.compile(
    r"(?:,[ \t]*)?[ \t]+(?:(?:" + "|".join(w.replace(" ", r"[ \t]+") for w in _DANGLING_WORDS) +
    r")(?:[ \t]+(?:a|an|the))?|(?:a|an|the))[ \t]+(?=[.,;:!?]|$)",
    re.I | re.M)


def fill(template: Any, values: Dict[str, Any]) -> str:
    """Replace ``{key}`` / ``{{key}}`` with ``values[key]``; unknown keys become ''.

    Never raises: stray braces and format specs are left alone (no str.format).
    """
    def repl(m: "re.Match[str]") -> str:
        v = values.get(m.group(1) or m.group(2))
        return "" if v is None else str(v)
    return PLACEHOLDER_RE.sub(repl, str(template or ""))


def tidy(text: Any) -> str:
    """Clean up rendered copy: whitespace, blank lines, orphaned punctuation/connectors."""
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines: List[str] = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t]+", " ", line)
        line = _DANGLING_RE.sub("", line)
        line = re.sub(r"\(\s*\)|\[\s*\]", "", line)
        line = re.sub(r"\s+([,.;:!?])", r"\1", line)
        line = re.sub(r",+(?=[.!?;:])", "", line)
        line = re.sub(r",{2,}", ",", line)
        line = re.sub(r"(?<!\.)\.\.(?!\.)", ".", line)
        line = re.sub(r"^\s*(?:[,;:]+|\.(?!\.))\s*", "", line)
        line = re.sub(r"[ \t]+", " ", line).strip()
        if re.fullmatch(r"[,.;:\-–—]+", line):
            line = ""
        lines.append(line)
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def render(template: Any, values: Dict[str, Any]) -> str:
    return tidy(fill(template, values))


def sentence(text: Any) -> str:
    """Trim, capitalise the first letter and make sure it ends with punctuation."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if not t:
        return ""
    t = t[0].upper() + t[1:]
    if t[-1] not in ".!?…":
        t += "."
    return t


def sequence_steps(ctx: Any) -> List[Dict[str, Any]]:
    """``writer.sequence`` as a list of dicts (each with an int ``day``)."""
    seq = ctx.playbook.writer.get("sequence") or []
    out: List[Dict[str, Any]] = []
    for i, step in enumerate(seq):
        step = step if isinstance(step, dict) else {}
        try:
            day = int(step.get("day"))
        except (TypeError, ValueError):
            day = i + 1
        out.append({"day": day, "purpose": str(step.get("purpose") or "")})
    return out


def signoff_for(lead: Lead, ctx: Any, values: Optional[Dict[str, str]] = None) -> Tuple[str, str]:
    """``(signature, footer)`` exactly as writers append them to every body."""
    v = values if values is not None else build_values(lead, ctx)
    return v.get("signature", ""), v.get("footer", "")


def append_signoff(body: str, signature: str = "", footer: str = "") -> str:
    parts = [p.strip("\n") for p in (body.rstrip(), signature, footer) if p and p.strip()]
    return "\n\n".join(parts)


def strip_signoff(body: str, signature: str = "", footer: str = "") -> str:
    """Remove the appended signature/footer from the end of a body (if present)."""
    text = (body or "").rstrip()
    for block in (footer, signature):
        b = (block or "").strip()
        if b and text.endswith(b):
            text = text[: len(text) - len(b)].rstrip()
    return text


# --- lead -> placeholder values ---------------------------------------------------------------

_LEGAL_SUFFIX_RE = re.compile(
    r"[\s,]+(?:inc\.?|incorporated|llc\.?|l\.l\.c\.?|ltd\.?|limited|plc\.?|corp\.?|corporation|gmbh|"
    r"ag|s\.a\.?|sa|s\.a\.s\.?|sas|b\.v\.?|bv|n\.v\.?|nv|pty\.?|pte\.?|llp|lp)\s*$", re.I)

_TITLE_CUT_RE = re.compile(r"\s+[-–—|]\s+|\s*[|•]\s*|,\s+|\s+\(|\s+\[")
_TITLE_NOISE_RE = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_LEAD_VERB_RE = re.compile(
    r"^(?:(?:has\s+|have\s+)?(?:adopted|adopts|adopting|started\s+using|starts\s+using|now\s+using|using|uses|"
    r"implemented|implements|implementing|migrated\s+to|migrating\s+to|moved\s+to|switched\s+to|"
    r"added|installed|deployed|rolled\s+out|rolling\s+out))\s+", re.I)

_ROUND_RE = re.compile(r"\b(pre[\s-]?seed|seed|series\s+[a-h]\b\+?|ipo|growth\s+round|growth\s+equity|"
                       r"bridge\s+round|angel\s+round|venture\s+round|debt\s+financing)", re.I)
_AMOUNT_UNITS = {"k": "K", "m": "M", "mm": "M", "million": "M", "b": "B", "bn": "B", "billion": "B"}
_AMOUNT_RE = re.compile(r"(?:[$£€]\s?\d[\d,.]*\s?(?:k|m|mm|bn|b|million|billion)?\b|"
                        r"\b\d[\d,.]*\s?(?:m|mm|bn|million|billion)\b)", re.I)


def company_display_name(name: str) -> str:
    """'Acme Widgets Ltd.' -> 'Acme Widgets' (case kept; legal suffixes dropped)."""
    out = (name or "").strip()
    for _ in range(3):
        stripped = _LEGAL_SUFFIX_RE.sub("", out).strip().rstrip(",").strip()
        if not stripped or stripped == out:
            break
        out = stripped
    return out or (name or "").strip()


def nice_name(first_name: str) -> str:
    """Tidy a first name ('JANE' -> 'Jane'); '' for junk (emails, digits, initials)."""
    n = re.sub(r"\s+", " ", (first_name or "").strip()).strip(".,;")
    if not n or "@" in n or any(ch.isdigit() for ch in n) or len(n.replace(".", "")) < 2:
        return ""
    if n.isupper() or n.islower():
        n = "-".join(" ".join(w.capitalize() for w in part.split(" ")) for part in n.split("-"))
    return n


def clean_title(title: str, max_words: int = 6) -> str:
    """'Senior Accountant - Hybrid (m/f/d) | £45k' -> 'Senior Accountant'."""
    t = re.sub(r"\s+", " ", (title or "")).strip()
    if not t:
        return ""
    head = _TITLE_CUT_RE.split(t, maxsplit=1)[0].strip()
    t = _TITLE_NOISE_RE.sub("", head or t)
    t = re.sub(r"\s+", " ", t).strip(" -–—|,:;/")
    words = t.split()
    if len(words) > max_words:
        t = " ".join(words[:max_words])
    letters = [c for c in t if c.isalpha()]
    if letters and all(c.isupper() for c in letters) and any(len(w) > 4 for w in t.split()):
        # SHOUTED titles: keep short acronyms, title-case the rest
        t = " ".join(w if len(w) <= 3 else w.capitalize() for w in t.split())
    return t


def soft_lower(text: str) -> str:
    """Lowercase ordinary Capitalised words, keep acronyms/brands ('HR Manager' -> 'HR manager')."""
    out = []
    for w in (text or "").split():
        core = re.sub(r"[^A-Za-z]", "", w)
        plain_capitalised = len(core) > 1 and core[0].isupper() and core[1:].islower()
        out.append(w.lower() if plain_capitalised else w)
    return " ".join(out)


_AN_LETTERS = set("AEFHILMNORSX")  # acronyms read letter by letter: an HR, an SEO, a UX


def article(phrase: str) -> str:
    """'a' or 'an' for the phrase that follows."""
    words = (phrase or "").split()
    if not words:
        return "a"
    w = re.sub(r"[^A-Za-z]", "", words[0])
    if not w:
        return "a"
    if len(w) > 1 and w.isupper():
        return "an" if w[0] in _AN_LETTERS else "a"
    wl = w.lower()
    if wl.startswith(("hour", "honest", "honor", "honour", "heir")):
        return "an"
    if wl.startswith(("uni", "use", "usu", "uti", "ure", "eu", "one", "once", "ubi")):
        return "a"
    return "an" if wl[0] in "aeiou" else "a"


def age_phrase(age: Optional[int]) -> str:
    if age is None:
        return "recently"
    if age <= 0:
        return "today"
    if age == 1:
        return "yesterday"
    if age <= 6:
        return "this week"
    if age <= 30:
        return "recently"
    if age <= 60:
        return "a few weeks ago"
    return "a while back"


def funding_label(title: str) -> str:
    """'Acme raises $20M Series B led by X' -> '$20M Series B'; '' when nothing recognisable."""
    t = title or ""
    rnd = _ROUND_RE.search(t)
    amt = _AMOUNT_RE.search(t)
    parts: List[str] = []
    if amt:
        m = re.match(r"([$£€]?)\s?(\d[\d,.]*)\s?([a-z]*)", amt.group(0).strip(), re.I)
        if m:
            unit = _AMOUNT_UNITS.get(m.group(3).lower(), "")
            parts.append(f"{m.group(1)}{m.group(2).rstrip('.,')}{unit}")
    if rnd:
        r = re.sub(r"\s+", " ", rnd.group(1)).strip()
        rl = r.lower()
        if rl.startswith("series"):
            r = "Series " + r.split(" ", 1)[1].upper()
        elif rl == "ipo":
            r = "IPO"
        elif "seed" in rl:
            r = ("pre-seed" if rl.startswith("pre") else "seed") + " round"
        else:
            r = rl
        parts.append(r)
    elif parts:
        parts.append("raise")
    return " ".join(parts)


# Default copy per signal type. Rendered with the base placeholder values.
SIGNAL_PHRASES: Dict[str, str] = {
    "job_posting": "saw you're hiring {a_signal_title}",
    "job_posting_plural": "saw you have {signal_count} open roles, including {a_signal_title}",
    "funding": "congrats on the {funding_label}",
    "leadership_change": "noticed the recent leadership change at {company}",
    "expansion": "saw {company} is expanding into {signal_location}",
    "headcount_growth": "noticed the team at {company} has been growing fast",
    "tech_adoption": "noticed {company} has started using {signal_title}",
    "news": "saw the recent news about {company}",
    "review": "came across some recent reviews of {company}",
    "ad_activity": "noticed {company} has been running new ads recently",
    "website_change": "noticed the recent changes to the {company} website",
    "event": "saw {company} will be at {signal_title}",
    "custom": "saw the recent update from {company}",
    "none": "came across {company} and had a quick idea",
}

HYPOTHESES: Dict[str, str] = {
    "job_posting": "Usually that means the team is stretched until the seat is filled.",
    "job_posting_stale": "When a role stays open that long, the rest of the team usually ends up covering the gap.",
    "job_posting_plural": "Hiring for several roles at once usually means the team is stretched while those seats are open.",
    "funding": "New funding usually comes with ambitious targets and pressure to scale fast.",
    "leadership_change": "New leaders often take a fresh look at priorities, processes and partners in their first months.",
    "expansion": "Expanding usually means new capacity to build and processes that have to scale with it.",
    "headcount_growth": "Fast growth tends to strain processes that worked fine at a smaller size.",
    "tech_adoption": "New tools usually come with a bumpy rollout and a few gaps to fill.",
    "news": "Moments like this usually bring new priorities to the top of the list.",
    "review": "Feedback like that usually points to where the team could use support.",
    "ad_activity": "More ad spend usually means a push for growth, where every lead counts.",
    "website_change": "A refresh like that usually signals a new push or a new direction.",
    "event": "Events like that usually mean a busy stretch of prep and follow-up.",
    "custom": "Changes like this are usually when teams look for extra help.",
    "none": "Most teams like yours have more on their plate than people to handle it.",
}

TOPICS: Dict[str, str] = {
    "job_posting": "the {signal_title} hire",
    "job_posting_plural": "your open roles",
    "funding": "the next stage of growth",
    "leadership_change": "the leadership change",
    "expansion": "the expansion",
    "headcount_growth": "the team's growth",
    "tech_adoption": "the {signal_title} rollout",
    "news": "the recent news",
    "review": "the recent reviews",
    "ad_activity": "the new campaigns",
    "website_change": "the new website",
    "event": "{signal_title}",
    "custom": "what's next at {company}",
    "none": "what's next at {company}",
}

SUBJECTS: Dict[str, str] = {
    "job_posting": "{signal_title_lc} at {company}",
    "funding": "{company}'s {funding_label}",
    "leadership_change": "{company}'s new leadership",
    "expansion": "{company}'s expansion",
    "headcount_growth": "{company}'s growth",
    "tech_adoption": "{signal_title} at {company}",
    "event": "{company} at {signal_title}",
    "default": "idea for {company}",
}
MAX_SUBJECT_WORDS = 6

# Default bodies (niche-agnostic). Optional sentences come from *_line placeholders.
OPENER = ("Hi {first_name},\n\n{opener}\n\n{hypothesis} {value_line}\n\n{cta}")
BUMP = ("Hi {first_name},\n\nQuick follow-up on my note about {signal_topic}. {bump_line}\n\n{cta}")
VALUE = ("Hi {first_name},\n\nOne idea: I could put together a short, no-strings plan for how we'd "
         "support {company} through {signal_topic}. No call needed, just reply and I'll send it over."
         "\n\n{booking_line}")
CHECK_IN = ("Hi {first_name},\n\nChecking in once more on {signal_topic}. {value_line}\n\n{cta}")
CHECK_IN_2 = ("Hi {first_name},\n\nStill working through {signal_topic}? If so, happy to share how we'd "
              "help {company}.\n\n{cta}")
BREAKUP = ("Hi {first_name},\n\nI'll leave it here so I'm not cluttering your inbox. If the timing's "
           "better later on, just reply to this thread and I'll pick it back up.\n\n"
           "Good luck with {signal_topic}.")
FOLLOW_UPS = (BUMP, VALUE, CHECK_IN, CHECK_IN_2)

DEFAULT_CTA = "Worth a quick chat?"
DEFAULT_BUMP_LINE = "Happy to share what's worked for teams in a similar spot, if useful."


def _templates_cfg(ctx: Any) -> Dict[str, Any]:
    t = ctx.playbook.writer.get("templates") or {}
    return t if isinstance(t, dict) else {}


def _type_key(signal: Optional[Signal], plural: bool, stale: bool, table: Dict[str, str]) -> str:
    if signal is None:
        return "none"
    stype = (signal.type or "custom").strip().lower()
    if stype == SignalType.JOB_POSTING:
        if plural and "job_posting_plural" in table:
            return "job_posting_plural"
        if stale and "job_posting_stale" in table:
            return "job_posting_stale"
    return stype if stype in table else "custom"


def _pick(kind: str, key: str, signal: Optional[Signal], defaults: Dict[str, str], ctx: Any,
          data_key: str) -> str:
    """Signal data override > writer.templates.<kind> override > built-in default."""
    if signal is not None and isinstance(signal.data, dict) and signal.data.get(data_key):
        return str(signal.data[data_key])
    overrides = _templates_cfg(ctx).get(kind)
    if isinstance(overrides, dict):
        stype = (signal.type if signal else "none") or "custom"
        for k in (key, stype, "default"):
            if overrides.get(k):
                return str(overrides[k])
    return defaults.get(key) or defaults.get("custom", "")


def _stale_after(ctx: Any) -> int:
    try:
        return int(ctx.playbook.signals.get("stale_after_days") or 21)
    except (TypeError, ValueError):
        return 21


def build_values(lead: Lead, ctx: Any) -> Dict[str, str]:
    """All placeholder values for ``lead`` (see module docstring)."""
    offer = ctx.playbook.offer or {}
    company = lead.company
    contact = lead.contact
    sig = lead.top_signal

    def o(key: str) -> str:
        return str(offer.get(key) or "").strip()

    v: Dict[str, str] = {}
    v["first_name"] = nice_name(contact.first_name if contact else "") or "there"
    v["last_name"] = (contact.last_name if contact else "").strip()
    v["full_name"] = (contact.full_name if contact else "").strip()
    v["contact_title"] = (contact.title if contact else "").strip()
    v["company_full"] = (company.name or "").strip()
    v["company"] = company_display_name(company.name) or company.domain or "your team"
    v["domain"] = company.domain or ""
    v["industry"] = (company.industry or "").strip()
    v["employees"] = str(company.employees) if company.employees is not None else ""
    v["location"] = (company.location or company.country or (sig.location if sig else "") or "").strip()
    for key in ("service", "value_prop", "booking_link", "sender_name", "sender_company", "sender_title",
                "sender_website"):
        v[key] = o(key)

    # signal facts
    same_type = [s for s in company.signals if sig is not None and s.type == sig.type]
    count = len({s.fingerprint for s in same_type}) if same_type else 0
    age = sig.age_days(ctx.today) if sig is not None else None
    title = clean_title(sig.title) if sig is not None else ""
    if sig is not None and sig.type == SignalType.TECH_ADOPTION:
        title = _LEAD_VERB_RE.sub("", title).strip() or title
    plural = sig is not None and sig.type == SignalType.JOB_POSTING and count >= 2
    stale = sig is not None and (sig.reposted or (age is not None and age >= _stale_after(ctx)))
    v["signal_type"] = sig.type if sig is not None else ""
    v["signal_title"] = title
    v["signal_title_full"] = (sig.title or "").strip() if sig is not None else ""
    v["signal_title_lc"] = soft_lower(title)
    v["a_signal_title"] = f"{article(title)} {title}" if title else ""
    v["signal_count"] = str(count) if count else ""
    v["signal_location"] = (sig.location or "").strip() if sig is not None else ""
    v["signal_url"] = (sig.url or "").strip() if sig is not None else ""
    v["signal_age_phrase"] = age_phrase(age) if sig is not None else ""
    label = funding_label(sig.title) if sig is not None and sig.type == SignalType.FUNDING else ""
    if sig is not None and sig.type == SignalType.FUNDING and not label:
        label = "recent funding round"  # unrecognised titles are headlines or generic words
    v["funding_label"] = label

    # signal-driven copy
    phrase_key = _type_key(sig, plural, stale, SIGNAL_PHRASES)
    v["signal_phrase"] = render(_pick("signal_phrases", phrase_key, sig, SIGNAL_PHRASES, ctx, "phrase"), v)
    hyp_key = _type_key(sig, plural, stale, HYPOTHESES)
    v["hypothesis"] = sentence(render(_pick("hypotheses", hyp_key, sig, HYPOTHESES, ctx, "hypothesis"), v))
    topic_key = _type_key(sig, plural, stale, TOPICS)
    v["signal_topic"] = render(_pick("topics", topic_key, sig, TOPICS, ctx, "topic"), v) or \
        render(TOPICS["none"], v)
    opener = v["signal_phrase"]
    if phrase_key == "job_posting" and age is not None and age <= 6 and not stale and not \
            (isinstance(sig.data, dict) and sig.data.get("phrase")):
        opener = f"{opener} {v['signal_age_phrase']}"
    v["opener"] = sentence(tidy(opener))

    # offer-driven copy (offer strings may use placeholders too)
    v["cta"] = sentence(render(o("cta"), v)) or DEFAULT_CTA
    v["proof"] = render(o("proof"), v)
    v["proof_line"] = sentence(v["proof"])
    v["value_prop"] = render(v["value_prop"], v)
    v["value_line"] = _value_line(v)
    v["booking_line"] = (f"If it's easier, you can grab a time here: {v['booking_link']}"
                         if v["booking_link"] else "")
    v["bump_line"] = v["proof_line"] or DEFAULT_BUMP_LINE
    v["signature"] = _signature(offer, v)
    v["footer"] = render(o("footer"), v)
    return v


def _value_line(v: Dict[str, str]) -> str:
    vp, svc = v.get("value_prop", "").strip(), v.get("service", "").strip()
    if vp:
        low = vp.lower()
        if low.startswith(("we ", "we'", "we’", "our ", "i ", "i'", "i’", "my ")) or vp[-1] in ".!?":
            return sentence(vp)
        first = vp.split()[0]
        if first[:1].isupper() and first[1:] == first[1:].lower():
            vp = vp[0].lower() + vp[1:]
        return sentence(f"We help teams like {v.get('company') or 'yours'} {vp}")
    if svc:
        who = v.get("sender_company")
        return sentence(f"At {who}, we focus on {svc}" if who else f"We focus on {svc}")
    return ""


def _signature(offer: Dict[str, Any], v: Dict[str, str]) -> str:
    custom = str(offer.get("signature") or "").strip()
    if custom:
        return render(custom, v)
    role = ", ".join(x for x in (v.get("sender_title", ""), v.get("sender_company", "")) if x)
    return "\n".join(x for x in (v.get("sender_name", ""), role) if x)


def default_subject(lead: Lead, values: Dict[str, str]) -> str:
    """Short step-1 subject (<= 6 words) for the lead's top signal."""
    sig = lead.top_signal
    stype = (sig.type if sig is not None else "") or ""
    candidates = [SUBJECTS[stype]] if stype in SUBJECTS else []
    candidates.append(SUBJECTS["default"])
    for tpl in candidates:
        needed = [m.group(1) or m.group(2) for m in PLACEHOLDER_RE.finditer(tpl)]
        if any(not values.get(k) for k in needed):
            continue
        subject = render(tpl, values)
        if subject and len(subject.split()) <= MAX_SUBJECT_WORDS:
            return subject
    return " ".join(render(SUBJECTS["default"], values).split()[:MAX_SUBJECT_WORDS])


# --- the writer -----------------------------------------------------------------------------

def _default_plan(n: int) -> List[str]:
    """Default body templates for an n-step sequence (breakup always last)."""
    if n <= 0:
        return []
    if n == 1:
        return [OPENER]
    middle = [FOLLOW_UPS[min(i, len(FOLLOW_UPS) - 1)] for i in range(n - 2)]
    return [OPENER] + middle + [BREAKUP]


class TemplateWriter(Writer):
    """Offline, niche-agnostic copy from templates (see module docstring)."""

    name = "template"
    offline = True

    def _overrides(self) -> Tuple[List[Dict[str, Optional[str]]], List[Optional[str]]]:
        """(override steps, explicit subjects) from ``writer.templates``."""
        tcfg = _templates_cfg(self.ctx)
        raw_steps = tcfg.get("steps") or []
        if isinstance(raw_steps, (str, dict)):
            raw_steps = [raw_steps]
        steps: List[Dict[str, Optional[str]]] = []
        if isinstance(raw_steps, list):
            for i, s in enumerate(raw_steps):
                if isinstance(s, str) and s.strip():
                    steps.append({"subject": None, "body": s})
                elif isinstance(s, dict) and str(s.get("body") or "").strip():
                    subj = s.get("subject")
                    steps.append({"subject": None if subj is None else str(subj), "body": str(s["body"])})
                else:
                    self.log.warning("writer.templates.steps[%d] ignored: needs a body string", i)
        else:
            self.log.warning("writer.templates.steps ignored: expected a list")
        subjects = tcfg.get("subjects") or []
        if isinstance(subjects, str):
            subjects = [subjects]
        subjects = [None if s is None else str(s) for s in subjects] if isinstance(subjects, list) else []
        if tcfg.get("subject") and not subjects[:1]:
            subjects = [str(tcfg["subject"])] + subjects[1:]
        return steps, subjects

    def write(self, lead: Lead) -> WriterOutput:
        from .guardrails import check_sequence  # local import: guardrails imports this module

        ctx = self.ctx
        seq = sequence_steps(ctx)
        values = build_values(lead, ctx)
        signature, footer = values["signature"], values["footer"]
        overrides, subjects = self._overrides()
        plan = _default_plan(len(seq))

        messages: List[Message] = []
        for i, step in enumerate(seq):
            subject_tpl: Optional[str] = subjects[i] if i < len(subjects) else None
            if len(overrides) >= 2 or (overrides and i == 0):
                tpl = overrides[min(i, len(overrides) - 1)]
                body_tpl = tpl["body"] or ""
                if i < len(overrides) and tpl.get("subject") is not None:
                    subject_tpl = tpl["subject"]
            else:
                body_tpl = plan[i]
            step_values = dict(values, step=str(i + 1), day=str(step["day"]))
            body = render(body_tpl, step_values)
            if subject_tpl is not None and subject_tpl.strip():
                subject = render(subject_tpl, step_values).replace("\n", " ")
            else:
                subject = default_subject(lead, step_values) if i == 0 else ""
            messages.append(Message(step=i + 1, day=step["day"], subject=subject,
                                    body=append_signoff(body, signature, footer)))

        warnings = ["template: " + p for p in check_sequence(messages, lead, ctx)]
        return WriterOutput(messages=messages, personalization=values["opener"],
                            hypothesis=values["hypothesis"], writer=self.name, warnings=warnings)


__all__ = ["TemplateWriter", "append_signoff", "build_values", "clean_title", "company_display_name",
           "default_subject", "fill", "render", "sequence_steps", "signoff_for", "strip_signoff", "tidy"]
