"""Reports: the "micro-demo" sales asset, funnel stats and run history.

Demo report (``demo_report`` / ``write_demo``)
----------------------------------------------
A one-page "Live opportunities for <prospect>" document you send a prospect
to show what the engine finds for *their* business: a one-paragraph summary
(how many companies show live signals, for how many the decision-maker was
identified, as of ``ctx.today``) followed by the top opportunities. Each
opportunity shows the company (domain, size, industry, location), its
signal(s) with age in days and link, the "why now" hypothesis, the
decision-maker (name, title, masked email + verification status), the score
with tier and reasons, and the first email of the sequence (subject + body).
The footer carries ``offer.cta``, ``offer.booking_link`` and the sender.

Opportunities are picked one per company, preferring leads that already
have a written sequence, then leads with a decision-maker, then the highest
score. Rendered twice from the same data: Markdown and a standalone,
responsive HTML page (inline CSS, no external assets, every dynamic value
escaped with ``html.escape``; only http(s) links are ever rendered). With no
leads both renderings explain that there are no opportunities yet and what
to change.

``mask_email('jane.doe@acme.com') -> 'j*******@acme.com'`` keeps contact
details private in a preview (``mask=False`` shows them).

Funnel + runs (``funnel_report`` / ``runs_report``)
---------------------------------------------------
Plain-text, aligned tables for the CLI (``leadgen stats``) built from
``Store.funnel`` / ``Store.list_runs``: stage counts with conversion vs the
previous stage and vs exported, key rates (reply, positive, booked), replies
by category, tiers and lost leads. Divisions by zero render as ``-``.
"""
from __future__ import annotations

import html
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote, urlparse

from .models import EmailStatus, Lead, ReplyCategory, Stage, Tier
from .delivery.rows import is_guessed
from .utils import normalize_text, parse_date

#: How many signals to show per opportunity.
MAX_SIGNALS_SHOWN = 3
#: How many score reasons to show per opportunity.
MAX_REASONS_SHOWN = 5

SIGNAL_LABELS: Dict[str, str] = {
    "job_posting": "Hiring",
    "funding": "Funding",
    "leadership_change": "Leadership change",
    "expansion": "Expansion",
    "headcount_growth": "Headcount growth",
    "tech_adoption": "Tech adoption",
    "news": "News",
    "review": "Review",
    "ad_activity": "Ad activity",
    "website_change": "Website change",
    "event": "Event",
    "custom": "Signal",
}

EMAIL_STATUS_LABELS: Dict[str, str] = {
    EmailStatus.VALID: "verified",
    EmailStatus.RISKY: "catch-all domain, unconfirmed",
    EmailStatus.INVALID: "invalid",
    EmailStatus.UNKNOWN: "not verified yet",
}


# --- small helpers ---------------------------------------------------------------------

def mask_email(email: str) -> str:
    """'jane.doe@acme.com' -> 'j*******@acme.com' (first character + one * per hidden char)."""
    e = (email or "").strip()
    if not e:
        return ""
    local, at, domain = e.partition("@")
    if not at:
        return local[:1] + "*" * max(1, len(local) - 1)
    if not local:
        return "*@" + domain
    return local[0] + "*" * max(1, len(local) - 1) + "@" + domain


def slugify(text: str, max_len: int = 60) -> str:
    """'Acme Recruiting Ltd.' -> 'acme-recruiting-ltd'."""
    s = normalize_text(text).replace(" ", "-")
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s[:max_len].rstrip("-")


def _safe_url(url: Any) -> str:
    """Return ``url`` only when it is an absolute http(s) URL (never javascript:, data:, ...)."""
    u = str(url or "").strip()
    if not u:
        return ""
    try:
        p = urlparse(u)
    except ValueError:
        return ""
    return u if p.scheme.lower() in ("http", "https") and p.netloc else ""


def _site_url(value: Any) -> str:
    """Like ``_safe_url`` but accepts a bare domain ('growth.co' -> 'https://growth.co')."""
    v = str(value or "").strip()
    if v and "://" not in v and "." in v and not re.search(r"[\s<>\"']", v):
        v = "https://" + v.lstrip("/")
    return _safe_url(v)


def _md_url(url: str) -> str:
    """Percent-encode characters that could break out of a Markdown ``(<url>)`` destination."""
    return quote(url, safe=":/?#[]@!$&'()*+,;=%~-._")


def _fmt_date(d: date) -> str:
    return f"{d.day} {d.strftime('%B %Y')}"


def _age_phrase(days: Optional[int]) -> str:
    if days is None:
        return "date unknown"
    if days <= 0:
        return "today"
    if days == 1:
        return "1 day ago"
    return f"{days} days ago"


def _humanize(name: str) -> str:
    words = re.split(r"[-_.\s]+", name or "")
    return " ".join(w[:1].upper() + w[1:] for w in words if w)


def signal_label(signal_type: str) -> str:
    t = (signal_type or "").strip()
    return SIGNAL_LABELS.get(t) or (t.replace("_", " ").capitalize() if t else "Signal")


def _employees(n: Optional[int]) -> str:
    if n is None:
        return ""
    return "1 employee" if n == 1 else f"{n:,} employees"


def _location(company: Any) -> str:
    loc = (company.location or "").strip()
    country = (company.country or "").strip()
    if country and normalize_text(country) not in normalize_text(loc):
        return f"{loc}, {country}" if loc else country
    return loc


def _rank(lead: Lead) -> Tuple[int, int, int, int]:
    has_contact = bool(lead.contact and (lead.contact.full_name or lead.contact.email))
    return (1 if lead.messages else 0, 1 if has_contact else 0,
            1 if lead.company.signals else 0, int(lead.score or 0))


def select_opportunities(leads: Sequence[Lead], top: int = 5) -> List[Lead]:
    """Best lead per company, ranked: has a sequence > has a decision-maker > has signals > score."""
    best: Dict[str, Lead] = {}
    order: List[str] = []
    for lead in leads:
        key = lead.company.key
        if key not in best:
            best[key] = lead
            order.append(key)
        elif _rank(lead) > _rank(best[key]):
            best[key] = lead
    ranked = sorted((best[k] for k in order), key=_rank, reverse=True)
    return ranked[:top] if top and top > 0 else ranked


# --- view model (shared by the Markdown and HTML renderers) ----------------------------

def _opportunity(lead: Lead, today: date, mask: bool) -> Dict[str, Any]:
    c, ct = lead.company, lead.contact
    signals = []
    for s in (c.signals or [])[:MAX_SIGNALS_SHOWN]:
        signals.append({
            "label": signal_label(s.type),
            "title": (s.title or "").strip() or signal_label(s.type),
            "age_days": s.age_days(today),
            "age": _age_phrase(s.age_days(today)),
            "url": _safe_url(s.url),
            "reposted": bool(s.reposted),
        })
    contact = None
    if ct and (ct.full_name or ct.email or ct.title):
        email = (ct.email or "").strip()
        contact = {
            "name": ct.full_name or "",
            "title": ct.title or "",
            "email": (mask_email(email) if mask else email),
            "status": ("guessed-unverified" if is_guessed(ct)
                       else EMAIL_STATUS_LABELS.get(ct.email_status, ct.email_status or "")) if email else "",
        }
    email_msg = None
    msgs = sorted(lead.messages or [], key=lambda m: m.step if isinstance(m.step, int) else 0)
    if msgs:
        email_msg = {"subject": (msgs[0].subject or "").strip(), "body": (msgs[0].body or "").strip()}
    meta = [x for x in (_employees(c.employees), (c.industry or "").strip(), _location(c)) if x]
    reasons = [r for r in (lead.breakdown.reasons if lead.breakdown else []) if r][:MAX_REASONS_SHOWN]
    return {
        "company": c.name or c.domain or "Unnamed company",
        "domain": c.domain or "",
        "website": _site_url(c.website) or (f"https://{c.domain}" if c.domain else ""),
        "meta": meta,
        "signals": signals,
        "extra_signals": max(0, len(c.signals or []) - MAX_SIGNALS_SHOWN),
        "why_now": (lead.hypothesis or "").strip(),
        "contact": contact,
        "score": int(lead.score or 0),
        "tier": lead.tier or "",
        "reasons": reasons,
        "email": email_msg,
    }


def _demo_data(leads: Sequence[Lead], ctx: Any, top: int, prospect: Optional[str],
               mask: bool) -> Dict[str, Any]:
    pb = ctx.playbook
    offer = pb.offer or {}
    companies: Dict[str, Dict[str, bool]] = {}
    for ld in leads:
        st = companies.setdefault(ld.company.key, {"signal": False, "contact": False, "valid": False})
        st["signal"] = st["signal"] or bool(ld.company.signals)
        if ld.contact and (ld.contact.full_name or ld.contact.email):
            st["contact"] = True
            if ld.contact.email and ld.contact.email_status == EmailStatus.VALID and not is_guessed(ld.contact):
                st["valid"] = True
    picked = select_opportunities(leads, top)
    # the "N of them" counts use the same base as the headline number
    base = [v for v in companies.values() if v["signal"]] or list(companies.values())
    return {
        "title_for": (prospect or "").strip() or _humanize(pb.name) or "you",
        "date": _fmt_date(ctx.today),
        "n_companies": len(companies),
        "n_signal": sum(1 for v in companies.values() if v["signal"]),
        "n_contact": sum(1 for v in base if v["contact"]),
        "n_valid": sum(1 for v in base if v["valid"]),
        "opportunities": [_opportunity(ld, ctx.today, mask) for ld in picked],
        "mask": mask,
        "sender_name": str(offer.get("sender_name") or "").strip(),
        "sender_title": str(offer.get("sender_title") or "").strip(),
        "sender_company": str(offer.get("sender_company") or "").strip(),
        "sender_website": _site_url(offer.get("sender_website")),
        "sender_website_text": str(offer.get("sender_website") or "").strip(),
        "cta": str(offer.get("cta") or "").strip(),
        "booking_link": _safe_url(offer.get("booking_link")),
    }


def _summary_segments(d: Dict[str, Any]) -> List[Tuple[str, bool]]:
    """The summary paragraph as ``(text, emphasised)`` segments (rendered per format)."""
    n_sig, n_all = d["n_signal"], d["n_companies"]
    segs: List[Tuple[str, bool]] = [(f"As of {d['date']}, ", False)]
    if n_sig:
        segs += [(f"{n_sig} {'company' if n_sig == 1 else 'companies'}", True),
                 (f" {'is' if n_sig == 1 else 'are'} showing live buying signals that fit the profile. ", False)]
    else:
        segs += [(f"{n_all} {'company' if n_all == 1 else 'companies'}", True),
                 (f" {'fits' if n_all == 1 else 'fit'} the profile. ", False)]
    shown = len(d["opportunities"])
    segs += [
        ("We identified the decision-maker at ", False),
        (str(d["n_contact"]), True),
        (f" of them ({d['n_valid']} with a verified email). ", False),
        (f"Below {'is the top one' if shown == 1 else f'are the top {shown}'}, ranked by how fresh and "
         f"strong the signal is, how well the company fits and whether we can reach the right person.", False),
    ]
    return segs


def _sender_line(d: Dict[str, Any]) -> str:
    who = ", ".join(x for x in (d["sender_name"], d["sender_title"]) if x)
    parts = [x for x in (who, d["sender_company"], d["sender_website_text"]) if x]
    return " · ".join(parts)


# --- Markdown --------------------------------------------------------------------------

_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>|])")


def _md(text: Any, strip: bool = True) -> str:
    """Escape inline Markdown/HTML-significant characters; newlines become spaces."""
    s = re.sub(r"\s*\n\s*", " ", str(text or ""))
    if strip:
        s = s.strip()
    return _MD_SPECIAL.sub(r"\\\1", s)


def _md_quote(text: str) -> List[str]:
    """Blockquote lines for an email body (hard line breaks kept, raw HTML neutralised)."""
    out = []
    for line in (text or "").splitlines():
        line = line.rstrip().replace("<", "\\<")
        out.append(f"> {line}  " if line else ">")
    return out


def _md_link(text: str, url: str) -> str:
    return f"[{_md(text)}](<{_md_url(url)}>)" if url else _md(text)


def _render_markdown(d: Dict[str, Any]) -> str:
    L: List[str] = [f"# Live opportunities for {_md(d['title_for'])}", ""]
    by = ", ".join(x for x in (d["sender_name"], d["sender_company"]) if x)
    L += [f"*Prepared {d['date']}" + (f" by {_md(by)}*" if by else "*"), ""]
    if not d["opportunities"]:
        L += [
            "**No live opportunities yet.** No company in the current data is showing a buying "
            f"signal that matches the profile (as of {d['date']}).",
            "",
            "What to try next:",
            "",
            "- widen the signal window (`signals.max_age_days`) or accept more signal types (`signals.types`)",
            "- loosen the ideal-customer filters (`icp`: locations, size, industries)",
            "- add or refresh sources, then run `leadgen run` again",
            "",
        ]
    else:
        L += ["".join(f"**{_md(t)}**" if em else _md(t, strip=False) for t, em in _summary_segments(d)).strip(), ""]
        for i, o in enumerate(d["opportunities"], 1):
            L += ["---", ""]
            head = f"## {i}. {_md(o['company'])}"
            if o["domain"]:
                head += f" ({_md_link(o['domain'], o['website'])})"
            L += [head, ""]
            if o["meta"]:
                L += [" · ".join(_md(m) for m in o["meta"]), ""]
            if o["signals"]:
                L += ["**Signals**", ""]
                for s in o["signals"]:
                    line = f"- **{_md(s['label'])}:** {_md(s['title'])} ({_md(s['age'])}"
                    line += ", re-posted: still open)" if s["reposted"] else ")"
                    if s["url"]:
                        line += " · " + _md_link("source", s["url"])
                    L.append(line)
                if o["extra_signals"]:
                    L.append(f"- …and {o['extra_signals']} more")
                L.append("")
            if o["why_now"]:
                L += [f"**Why now:** {_md(o['why_now'])}", ""]
            ct = o["contact"]
            if ct:
                who = ", ".join(_md(x) for x in (ct["name"], ct["title"]) if x) or "Identified"
                line = f"**Decision-maker:** {who}"
                if ct["email"]:
                    line += f" · `{ct['email']}`"
                    if ct["status"]:
                        line += f" ({_md(ct['status'])})"
                L += [line, ""]
            else:
                L += ["**Decision-maker:** not identified yet", ""]
            line = f"**Score:** {o['score']}/100"
            if o["tier"]:
                line += f" · {_md(o['tier'])}"
            if o["reasons"]:
                line += " — " + "; ".join(_md(r) for r in o["reasons"])
            L += [line, ""]
            if o["email"]:
                L += ["**Suggested first email**", ""]
                if o["email"]["subject"]:
                    L += [f"> **Subject:** {_md(o['email']['subject'])}", ">"]
                L += _md_quote(o["email"]["body"])
                L.append("")
    L += ["---", "", "## Next step", ""]
    cta = d["cta"] or "Want a list like this for your business every week?"
    line = _md(cta)
    if d["booking_link"]:
        line += " " + _md_link("Book a time", d["booking_link"])
    L += [line, ""]
    sender = _sender_line(d)
    if sender:
        L += [_md(sender), ""]
    if d["mask"] and d["opportunities"]:
        L += ["*Contact emails are partially masked in this preview.*", ""]
    return "\n".join(L).rstrip() + "\n"


# --- HTML ------------------------------------------------------------------------------

_CSS = """
:root{--bg:#f4f5f7;--card:#ffffff;--text:#1f2933;--muted:#5f6b7a;--border:#e2e6ea;
--accent:#2457d6;--hot:#c2410c;--hot-bg:#ffedd5;--normal:#1d4ed8;--normal-bg:#dbeafe;
--skip:#4b5563;--skip-bg:#e5e7eb;--quote:#f8fafc}
@media (prefers-color-scheme:dark){:root{--bg:#111418;--card:#1a1f25;--text:#e6e9ee;
--muted:#9aa5b1;--border:#2c333b;--accent:#7aa2ff;--hot:#fdba74;--hot-bg:#431407;
--normal:#93c5fd;--normal-bg:#172554;--skip:#d1d5db;--skip-bg:#374151;--quote:#14181d}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
-webkit-text-size-adjust:100%}
.wrap{max-width:780px;margin:0 auto;padding:40px 20px 56px}
h1{font-size:1.9rem;line-height:1.2;margin:0 0 6px}
.sub{color:var(--muted);margin:0 0 20px;font-size:.95rem}
.summary{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px 20px;margin:0 0 28px}
.summary strong{color:var(--accent)}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:22px 22px 18px;margin:0 0 22px}
.card h2{font-size:1.25rem;margin:0 0 4px;display:flex;flex-wrap:wrap;gap:6px 10px;align-items:baseline}
.card h2 .n{color:var(--muted);font-weight:600}
.domain{font-size:.9rem;font-weight:400;color:var(--muted)}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.meta{color:var(--muted);font-size:.9rem;margin:0 0 14px}
.label{font-size:.75rem;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:700;margin:14px 0 6px}
ul.signals{list-style:none;padding:0;margin:0}
ul.signals li{padding:6px 0;border-top:1px solid var(--border)}
ul.signals li:first-child{border-top:0}
.sig-type{font-weight:700}
.age{color:var(--muted);font-size:.9rem;white-space:nowrap}
.flag{display:inline-block;font-size:.72rem;font-weight:700;padding:1px 7px;border-radius:999px;background:var(--hot-bg);color:var(--hot);margin-left:4px}
.row{display:flex;flex-wrap:wrap;gap:10px 18px;align-items:center}
.score{font-weight:800;font-size:1.1rem}
.tier{display:inline-block;font-size:.75rem;font-weight:700;text-transform:uppercase;padding:2px 9px;border-radius:999px;background:var(--skip-bg);color:var(--skip)}
.tier.hot{background:var(--hot-bg);color:var(--hot)}
.tier.normal{background:var(--normal-bg);color:var(--normal)}
ul.reasons{margin:6px 0 0;padding-left:20px;color:var(--muted);font-size:.9rem}
.email{background:var(--quote);border:1px solid var(--border);border-left:4px solid var(--accent);border-radius:8px;padding:12px 16px;margin-top:6px}
.email .subject{font-weight:700;margin-bottom:8px}
.email .body{white-space:pre-wrap;word-wrap:break-word;overflow-wrap:anywhere}
code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.9em}
.empty{background:var(--card);border:1px dashed var(--border);border-radius:12px;padding:22px}
.cta{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:22px;text-align:center;margin-top:30px}
.btn{display:inline-block;margin-top:12px;background:var(--accent);color:#fff;padding:10px 20px;border-radius:8px;font-weight:700}
.btn:hover{text-decoration:none;opacity:.92}
.sender{color:var(--muted);font-size:.9rem;margin-top:14px}
.note{color:var(--muted);font-size:.8rem;text-align:center;margin-top:18px}
@media (max-width:600px){.wrap{padding:24px 14px 40px}h1{font-size:1.5rem}.card{padding:18px 16px 14px}}
@media print{body{background:#fff}.card,.summary,.cta{break-inside:avoid;border-color:#ccc}.btn{color:#000;background:none;border:1px solid #000}}
"""


def _h(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _a(text: Any, url: str, cls: str = "") -> str:
    if not url:
        return _h(text)
    c = f' class="{_h(cls)}"' if cls else ""
    return f'<a{c} href="{_h(url)}" target="_blank" rel="noopener noreferrer">{_h(text)}</a>'


def _render_html(d: Dict[str, Any]) -> str:
    title = f"Live opportunities for {d['title_for']}"
    by = ", ".join(x for x in (d["sender_name"], d["sender_company"]) if x)
    P: List[str] = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{_h(title)}</title>",
        f"<style>{_CSS}</style>",
        "</head>",
        "<body>",
        '<main class="wrap">',
        f"<h1>{_h(title)}</h1>",
        f'<p class="sub">Prepared {_h(d["date"])}' + (f" by {_h(by)}" if by else "") + "</p>",
    ]
    if not d["opportunities"]:
        P += [
            '<section class="empty">',
            "<p><strong>No live opportunities yet.</strong> No company in the current data is showing "
            f"a buying signal that matches the profile (as of {_h(d['date'])}).</p>",
            "<p>What to try next:</p>",
            "<ul>",
            "<li>widen the signal window (<code>signals.max_age_days</code>) or accept more signal "
            "types (<code>signals.types</code>)</li>",
            "<li>loosen the ideal-customer filters (<code>icp</code>: locations, size, industries)</li>",
            "<li>add or refresh sources, then run <code>leadgen run</code> again</li>",
            "</ul>",
            "</section>",
        ]
    else:
        summary = "".join(f"<strong>{_h(t)}</strong>" if em else _h(t) for t, em in _summary_segments(d))
        P.append(f'<section class="summary"><p style="margin:0">{summary}</p></section>')
        for i, o in enumerate(d["opportunities"], 1):
            P.append('<article class="card">')
            head = f'<h2><span class="n">{i}.</span> <span>{_h(o["company"])}</span>'
            if o["domain"]:
                head += f' <span class="domain">{_a(o["domain"], o["website"])}</span>'
            P.append(head + "</h2>")
            if o["meta"]:
                P.append(f'<p class="meta">{" · ".join(_h(m) for m in o["meta"])}</p>')
            if o["signals"]:
                P.append('<div class="label">Live signals</div><ul class="signals">')
                for s in o["signals"]:
                    li = (f'<li><span class="sig-type">{_h(s["label"])}:</span> {_h(s["title"])} '
                          f'<span class="age">· {_h(s["age"])}</span>')
                    if s["reposted"]:
                        li += '<span class="flag">re-posted</span>'
                    if s["url"]:
                        li += " · " + _a("source", s["url"])
                    P.append(li + "</li>")
                if o["extra_signals"]:
                    P.append(f'<li class="age">…and {_h(o["extra_signals"])} more</li>')
                P.append("</ul>")
            if o["why_now"]:
                P.append(f'<div class="label">Why now</div><p style="margin:0">{_h(o["why_now"])}</p>')
            P.append('<div class="label">Decision-maker</div>')
            ct = o["contact"]
            if ct:
                who = ", ".join(_h(x) for x in (ct["name"], ct["title"]) if x) or "Identified"
                line = f"<strong>{who}</strong>" if ct["name"] else who
                if ct["email"]:
                    line += f" · <code>{_h(ct['email'])}</code>"
                    if ct["status"]:
                        line += f' <span class="age">({_h(ct["status"])})</span>'
                P.append(f'<p style="margin:0">{line}</p>')
            else:
                P.append('<p style="margin:0" class="age">Not identified yet</p>')
            tier = o["tier"] if o["tier"] in (Tier.HOT, Tier.NORMAL, Tier.SKIP) else ""
            P.append('<div class="label">Score</div><div class="row">'
                     f'<span class="score">{_h(o["score"])}/100</span>'
                     + (f'<span class="tier {_h(tier)}">{_h(o["tier"])}</span>' if o["tier"] else "")
                     + "</div>")
            if o["reasons"]:
                P.append('<ul class="reasons">' + "".join(f"<li>{_h(r)}</li>" for r in o["reasons"]) + "</ul>")
            if o["email"]:
                P.append('<div class="label">Suggested first email</div><div class="email">')
                if o["email"]["subject"]:
                    P.append(f'<div class="subject">Subject: {_h(o["email"]["subject"])}</div>')
                P.append(f'<div class="body">{_h(o["email"]["body"])}</div></div>')
            P.append("</article>")
    cta = d["cta"] or "Want a list like this for your business every week?"
    P.append(f'<section class="cta"><p style="margin:0;font-weight:600">{_h(cta)}</p>')
    if d["booking_link"]:
        P.append(_a("Book a time", d["booking_link"], "btn"))
    sender_bits = []
    who = ", ".join(x for x in (d["sender_name"], d["sender_title"]) if x)
    if who:
        sender_bits.append(_h(who))
    if d["sender_company"]:
        sender_bits.append(_h(d["sender_company"]))
    if d["sender_website"]:
        sender_bits.append(_a(d["sender_website_text"] or d["sender_website"], d["sender_website"]))
    elif d["sender_website_text"]:
        sender_bits.append(_h(d["sender_website_text"]))
    if sender_bits:
        P.append(f'<div class="sender">{" · ".join(sender_bits)}</div>')
    P.append("</section>")
    if d["mask"] and d["opportunities"]:
        P.append('<p class="note">Contact emails are partially masked in this preview.</p>')
    P += ["</main>", "</body>", "</html>"]
    return "\n".join(P) + "\n"


# --- public demo API -------------------------------------------------------------------

def demo_report(leads: Sequence[Lead], ctx: Any, top: int = 5, prospect: Optional[str] = None,
                mask: bool = True) -> Tuple[str, str]:
    """Build the "Live opportunities for <prospect>" report as ``(markdown, html)``.

    ``prospect`` defaults to the (humanised) playbook name; ``top`` limits the
    opportunities shown (``<= 0`` = all); ``mask`` hides most of each email
    address (see module docstring).
    """
    data = _demo_data(list(leads or []), ctx, top, prospect, mask)
    return _render_markdown(data), _render_html(data)


def write_demo(leads: Sequence[Lead], ctx: Any, out_dir: Any, top: int = 5,
               prospect: Optional[str] = None, mask: bool = True) -> Tuple[Path, Path]:
    """Write ``demo.md`` + ``demo.html`` (``demo-<prospect-slug>.*`` when a prospect is given)
    into ``out_dir`` (created if needed); returns ``(md_path, html_path)``."""
    md, page = demo_report(leads, ctx, top=top, prospect=prospect, mask=mask)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    slug = slugify(prospect or "")
    stem = f"demo-{slug}" if slug else "demo"
    md_path, html_path = out / f"{stem}.md", out / f"{stem}.html"
    md_path.write_text(md, encoding="utf-8")
    html_path.write_text(page, encoding="utf-8")
    return md_path, html_path


# --- text tables -----------------------------------------------------------------------

def _pct(n: Any, d: Any) -> str:
    try:
        n, d = float(n or 0), float(d or 0)
    except (TypeError, ValueError):
        return "-"
    if d <= 0:
        return "-"
    return f"{n / d * 100:.1f}%"


def _table(rows: Sequence[Sequence[Any]], right: Sequence[int] = (), indent: str = "  ") -> List[str]:
    """Align rows into columns (first row = header, underlined); ``right`` = right-aligned columns."""
    cells = [[str(c) for c in r] for r in rows]
    widths = [max(len(r[i]) for r in cells if i < len(r)) for i in range(max(len(r) for r in cells))]
    out = []
    for ri, r in enumerate(cells):
        parts = [(c.rjust(widths[i]) if i in right else c.ljust(widths[i])) for i, c in enumerate(r)]
        out.append((indent + "  ".join(parts)).rstrip())
        if ri == 0:
            out.append((indent + "  ".join("-" * w for w in widths)).rstrip())
    return out


def funnel_report(store: Any, playbook_name: Optional[str], since: Any = None) -> str:
    """Aligned text funnel for ``playbook_name`` (``None``/'' = all playbooks) since ``since``."""
    since_d = parse_date(since) if since else None
    f = store.funnel(playbook_name or None, since_d) or {}
    stages: Dict[str, int] = f.get("stages") or {}
    total = int(f.get("total_leads") or 0)
    who = playbook_name or "all playbooks"
    when = f"since {since_d.isoformat()}" if since_d else "all time"
    lines = [f"Funnel: {who} ({when})", f"Leads tracked: {total}", ""]
    if not total:
        lines.append("  No leads recorded yet - run `leadgen run` first.")
        return "\n".join(lines) + "\n"

    exported = int(stages.get(Stage.EXPORTED) or 0)
    exp_idx = Stage.ORDER.index(Stage.EXPORTED)
    rows: List[List[Any]] = [["stage", "leads", "vs previous", "vs exported"]]
    prev: Optional[int] = None
    for i, s in enumerate(Stage.ORDER):
        n = int(stages.get(s) or 0)
        rows.append([s, n, "-" if prev is None else _pct(n, prev), _pct(n, exported) if i >= exp_idx else "-"])
        prev = n
    lines += _table(rows, right=(1, 2, 3))

    replied = int(stages.get(Stage.REPLIED) or 0)
    positive = int(stages.get(Stage.POSITIVE) or 0)
    booked = int(stages.get(Stage.BOOKED) or 0)
    won = int(stages.get(Stage.WON) or 0)
    rates = [
        ["reply rate", "replied / exported", _pct(replied, exported)],
        ["positive rate", "positive / exported", _pct(positive, exported)],
        ["positive share of replies", "positive / replied", _pct(positive, replied)],
        ["booking rate", "booked / positive", _pct(booked, positive)],
        ["win rate", "won / booked", _pct(won, booked)],
    ]
    lines += ["", "Key rates"]
    lines += _table([["rate", "formula", "value"]] + rates, right=(2,))

    lines += ["", f"Lost: {int(f.get('lost') or 0)}"]

    replies: Dict[str, int] = {str(k): int(v or 0) for k, v in (f.get("replies") or {}).items()}
    lines += ["", "Replies by category"]
    if replies:
        known = [c for c in ReplyCategory.ALL if c in replies]
        others = sorted(k for k in replies if k not in ReplyCategory.ALL)
        n_rep = sum(replies.values())
        rows = [["category", "replies", "share"]]
        rows += [[c or "(none)", replies[c], _pct(replies[c], n_rep)] for c in known + others]
        lines += _table(rows, right=(1, 2))
    else:
        lines.append("  none yet")

    tiers: Dict[str, int] = {str(k): int(v or 0) for k, v in (f.get("tiers") or {}).items()}
    lines += ["", "Tiers"]
    if tiers:
        order = [t for t in (Tier.HOT, Tier.NORMAL, Tier.SKIP) if t in tiers]
        order += sorted(t for t in tiers if t not in order)
        rows = [["tier", "leads", "share"]] + [[t or "(none)", tiers[t], _pct(tiers[t], total)] for t in order]
        lines += _table(rows, right=(1, 2))
    else:
        lines.append("  none")
    return "\n".join(lines) + "\n"


_RUN_COLUMNS: Tuple[Tuple[str, str], ...] = (
    ("sourced", "sourced"), ("with_signal", "signal"), ("qualified", "icp"),
    ("enriched", "dm found"), ("verified", "verified"), ("hot", "hot"), ("normal", "normal"),
    ("written", "written"), ("exported", "exported"),
)


def _ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _duration(start: Optional[datetime], end: Optional[datetime]) -> str:
    if not start or not end:
        return "-"
    secs = max(0, int((end - start).total_seconds()))
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m {secs % 60:02d}s"
    return f"{secs // 3600}h {(secs % 3600) // 60:02d}m"


def runs_report(store: Any, playbook_name: Optional[str], limit: int = 10) -> str:
    """Recent runs (newest first) with their stage counts, as an aligned text table."""
    runs = store.list_runs(playbook_name or None, limit=limit) or []
    who = playbook_name or "all playbooks"
    if not runs:
        return f"No runs recorded yet for {who}.\n"
    header = ["run", "started"] + [label for _, label in _RUN_COLUMNS] + ["took", "notes"]
    if not playbook_name:
        header.insert(1, "playbook")
    rows: List[List[Any]] = [header]
    for r in runs:
        counts = r.get("counts") or {}
        meta = r.get("meta") or {}
        start, end = _ts(r.get("started_at")), _ts(r.get("finished_at"))
        notes = []
        if not r.get("finished_at"):
            notes.append("unfinished")
        if isinstance(meta, dict) and meta.get("dry_run"):
            notes.append("dry-run")
        if isinstance(meta, dict) and meta.get("limit"):
            notes.append(f"limit {meta['limit']}")
        row: List[Any] = [r.get("id", ""), start.strftime("%Y-%m-%d %H:%M") if start else str(r.get("started_at") or "-")]
        if not playbook_name:
            row.insert(1, r.get("playbook", ""))
        if isinstance(counts, dict) and counts:
            row += [int(counts.get(k) or 0) for k, _ in _RUN_COLUMNS]
        else:  # unfinished / crashed run: nothing recorded
            row += ["-" for _ in _RUN_COLUMNS]
        row += [_duration(start, end), ", ".join(notes)]
        rows.append(row)
    offset = 3 if not playbook_name else 2
    right = tuple(range(offset, offset + len(_RUN_COLUMNS) + 1))
    title = f"Recent runs: {who} (last {len(runs)})"
    return "\n".join([title, ""] + _table(rows, right=right)) + "\n"


__all__ = ["mask_email", "demo_report", "write_demo", "funnel_report", "runs_report",
           "select_opportunities", "slugify", "signal_label"]
