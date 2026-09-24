"""Reply handling: clean, classify and act on replies to our outbound emails.

Flow of ``handle_reply(reply, ctx)``::

    raw reply -> classify (rules | ai | auto) -> clean body -> link to a lead
              -> suggest_reply -> the category's actions -> alert -> store.save_reply

Categories (``models.ReplyCategory``) and the action taken for each
---------------------------------------------------------------------
positive     Interested: wants a call, asks for times/info, says yes.
             -> lead stage REPLIED then POSITIVE; alert "positive" with company,
                contact, snippet, booking link and a draft answer.
question     Asks something before deciding (price, how it works, ...).
             -> REPLIED; alert "question".
referral     Points us to someone else ("speak to Jane - jane@acme.com").
             -> REPLIED; when an address was given, a new lead for that person
                at the same company is saved (stage QUALIFIED, source
                "referral", note "referred by <from>") unless the address is
                suppressed or already a lead; alert "referral".
timing       Not now, maybe later ("try us next quarter").
             -> REPLIED; follow-up scheduled on ``follow_up_date`` (or today +
                ``replies.timing_default_days``).
ooo          Out-of-office auto-reply; ``follow_up_date`` = the return date.
             -> no stage change; follow-up the day after the return date (or
                today + ``replies.ooo_default_days``).
negative     Not interested / not a fit / already covered.
             -> REPLIED then LOST; the address is suppressed ("not interested").
unsubscribe  Asks to be removed / stop emailing / opt out.
             -> LOST; the address is suppressed ("unsubscribed").
bounce       Delivery failure. The sender is a mail server (mailer-daemon), so
             the bounced address is read from the notice body (Final-Recipient
             header, "wasn't delivered to x@y", Outlook/Postfix formats, ...),
             preferring an address that belongs to a known lead.
             -> LOST; the address is suppressed ("bounced") and cached as
                ``invalid`` in the verification cache.
other        Anything else (acknowledgements, unclear).
             -> REPLIED - except automated delivery notices ("delivery
                delayed, will retry"), which change nothing.

Every reply is also passed to ``leadgen.notify.notify`` under its category
name, so ``notify.on`` decides what alerts (default: positive, referral,
question; add e.g. ``timing`` or ``negative`` to be told about those too).
Replies from unknown senders are still classified, saved and suppressed where
relevant. A reply already stored (same playbook, sender, ``received_at`` and
body) is not processed twice, so webhook retries and CSV re-imports are safe.

Classifiers (``replies.classifier``)
------------------------------------
rules  Deterministic keyword/regex rules (``classify_rules``). Precedence:
       bounce > ooo > unsubscribe > referral > negative > timing > positive >
       question > other. Negations win ("not interested" / "not sure" are never
       positive; the negation scope ends at punctuation or "but"). A soft
       decline with a concrete time ("no thanks - try us next quarter") is
       timing; a hard one ("not a fit", "please don't") stays negative.
ai     The playbook's LLM (``writer.provider``/``model``) via ``classify_ai``.
       The answer is validated (category, YYYY-MM-DD date); any error or
       malformed answer falls back to the rules.
auto   (default) Machine-generated mail - bounces, clear out-of-office
       replies, explicit unsubscribes, anything from a mailer-daemon - is
       decided by the rules (confidence >= 0.8). Everything else goes to the
       AI when an LLM is configured and this is not a dry run, else the rules.

Dates: relative expressions ("back on October 3", "returning 3rd October",
"until 10/03/2026", "until Monday", "next quarter", "in 3 weeks", "after
Q1") resolve against ``ctx.today`` to the next occurrence. Numeric dates are
read both ways (month/day and day/month) and the nearest plausible one wins,
so nothing depends on the sender's country.

Config keys (``playbook.replies``)
----------------------------------
classifier           rules | ai | auto (default auto).
timing_default_days  Follow-up delay for "not now" replies without a date (30).
ooo_default_days     Follow-up delay for out-of-office replies without a
                     return date (optional, default 7).
``offer.sender_company/sender_name/service/value_prop/booking_link/cta/
signature/sender_website/language`` feed the AI prompt, the drafts and the
alerts (``sender_website`` also marks our own addresses, which are never taken
as referral/bounce addresses).

Inputs
------
``load_replies_csv(path)`` reads an exported replies CSV and
``parse_webhook_payload(payload)`` turns an Instantly / Smartlead / generic
webhook body into a ``Reply`` (see their docstrings for the accepted shapes).
"""
from __future__ import annotations

import copy
import csv
import html
import json
import re
from dataclasses import fields, replace
from datetime import date, datetime, timedelta, timezone
from email.utils import parseaddr
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Pattern, Sequence, Tuple

from .models import Company, Contact, Lead, Reply, ReplyCategory, Stage
from .notify import notify
from .utils import get_path, is_personal_email, is_valid_email, normalize_domain, strip_accents, truncate

RC = ReplyCategory

#: Categories the rules decide on their own in ``auto`` mode (machine-generated mail).
AUTO_RULE_CATEGORIES = (RC.BOUNCE, RC.OOO, RC.UNSUBSCRIBE)
#: Minimum rule confidence for ``auto`` mode to skip the AI for those categories.
AUTO_RULES_MIN_CONFIDENCE = 0.8
DEFAULT_TIMING_DAYS = 30
DEFAULT_OOO_DAYS = 7
MAX_AI_REPLY_CHARS = 4000
MAX_STORED_RAW_CHARS = 2000
#: Longer inputs are cut before parsing (what a person wrote is at the top; this
#: bounds the work done on untrusted webhook payloads).
MAX_INPUT_CHARS = 500_000

# Linear-time address finder: a match may only start at the beginning of a run of
# local-part characters, and parts are length-capped (RFC 5321), so long junk
# runs cannot trigger quadratic rescans.
_EMAIL_PAT = (r"(?<![A-Za-z0-9._%+'\-])[A-Za-z0-9._%+'\-]{1,64}@[A-Za-z0-9\-]{1,63}"
              r"(?:\.[A-Za-z0-9\-]{1,63}){0,8}\.[A-Za-z]{2,24}(?![A-Za-z0-9\-])")
_EMAIL_RE = re.compile(_EMAIL_PAT)


# =============================================================================
# Text cleaning
# =============================================================================

_HTML_HINT = re.compile(
    r"<\s*/?\s*(?:html|body|head|div|p|br|span|table|tbody|tr|td|blockquote|a|b|i|u|strong|em|font"
    r"|ul|ol|li|hr|img|meta|style|h[1-6]|center|section)\b[^<>]*>", re.I)
_HTML_DROP_OPEN = re.compile(r"<(head|style|script|title)\b[^<>]*>", re.I)
# Containers mail clients wrap the quoted history in (always at the end of the reply).
_HTML_QUOTE_START = re.compile(
    r"<[a-z][a-z0-9]*\b[^<>]{0,500}?\b(?:class|id)\s*=\s*[\"']?[^\"'<>]{0,200}?"
    r"(?:gmail_quote|gmail_extra|yahoo_quoted|moz-cite-prefix|divRplyFwdMsg|appendonsend"
    r"|OutlookMessageHeader|protonmail_quote|zmail_extra|reply-quote|x_divRplyFwdMsg)", re.I)
_BLOCKQUOTE_INNER = re.compile(r"<blockquote\b[^<>]*>(?:(?!<blockquote\b).)*?</blockquote\s*>", re.I | re.S)
_BLOCK_BREAK = re.compile(r"<\s*(?:br|/p|/div|/li|/tr|/h[1-6]|/table|/ul|/ol|hr)\b[^<>]*>", re.I)
_LIST_ITEM = re.compile(r"<\s*li\b[^<>]*>", re.I)
_TAG = re.compile(r"<[^<>]*>")

_QUOTE_VERBS = r"(?:wrote|writes|schrieb|a\s+écrit|escribió|ha\s+scritto|schreef|escreveu|skrev|napisał)"
_QUOTE_HEADER_ON = re.compile(
    rf"^(?:on|am|le|el|il|op|em|den|på|dnia)\b.{{0,300}}?\b{_QUOTE_VERBS}\s*:?\s*$", re.I)
_QUOTE_HEADER_BARE = re.compile(rf"^.{{0,300}}\b{_QUOTE_VERBS}\s*:\s*$", re.I)
_QUOTE_HEADER_START = re.compile(r"^(?:on|am|le|el|il|op|em|den|på|dnia)\b", re.I)
_ORIGINAL_MSG = re.compile(
    r"^[-_=*\s]*(?:original\s+message|forwarded\s+message|begin\s+forwarded\s+message"
    r"|ursprüngliche\s+nachricht|message\s+d'origine|mensaje\s+original|messaggio\s+originale"
    r"|oorspronkelijk\s+bericht|mensagem\s+original)[-_=*:\s]*$", re.I)
_HEADER_FROM = re.compile(r"^\*?(?:from|von|de|da|van|från|fra|od)\s*:\*?\s*\S", re.I)
_HEADER_NEXT = re.compile(
    r"^\*?(?:sent|date|to|subject|cc|gesendet|datum|an|betreff|envoyé|à|objet|enviado|fecha|para"
    r"|asunto|inviato|data|oggetto|verzonden|onderwerp|aan|skickat|ämne|till)\s*:", re.I)
_UNDERSCORE_SEP = re.compile(r"^_{5,}\s*$")
_MOBILE_SIG = re.compile(
    r"^(?:sent\s+from\s+my\b|sent\s+from\s+(?:outlook|mail\s+for|yahoo|gmail|samsung|proton)"
    r"|get\s+outlook\s+for\b|sent\s+via\s+\w+|envoyé\s+de\s+mon|von\s+meinem\s+.{0,30}gesendet"
    r"|enviado\s+desde\s+mi)", re.I)
_SIGNOFF = re.compile(
    r"^(?i:best|best\s+regards|best\s+wishes|kind\s+regards|warm\s+regards|warmest\s+regards|regards"
    r"|many\s+thanks|thanks|thanks\s+again|thank\s+you|thx|cheers|sincerely|yours\s+sincerely"
    r"|yours\s+truly|all\s+the\s+best|br|talk\s+soon|speak\s+soon|cordially|respectfully)"
    r"[ \t]*[,.!\-–—]*[ \t]*(?P<name>[A-Z][\w'’.\-]*(?:[ \t]+[A-Z][\w'’.\-]*){0,2})?[ \t]*$")
_NAMEISH_LINE = re.compile(r"^[-–—~\s]*[A-Z][\w'’.\-]*(?:\s+[A-Z][\w'’.\-]*){0,3}\s*,?$")
_WS = re.compile(r"\s+")


def _looks_like_html(s: str) -> bool:
    return bool(_HTML_HINT.search(s))


def _drop_comments(s: str) -> str:
    out, pos = [], 0
    while True:
        start = s.find("<!--", pos)
        if start == -1:
            out.append(s[pos:])
            return " ".join(out)
        out.append(s[pos:start])
        end = s.find("-->", start + 4)
        if end == -1:
            return " ".join(out)
        pos = end + 3


def _drop_elements(s: str) -> str:
    """Remove <head>/<style>/<script>/<title> elements (linear, tolerates unclosed tags)."""
    out, pos = [], 0
    unclosed = set()  # once a closing tag is missing, later openings can't find it either
    while True:
        m = _HTML_DROP_OPEN.search(s, pos)
        if not m:
            out.append(s[pos:])
            return " ".join(out)
        out.append(s[pos:m.start()])
        name = m.group(1).lower()
        close = None if name in unclosed else re.compile(rf"</{name}\s*>", re.I).search(s, m.end())
        if close is None:
            unclosed.add(name)
        pos = close.end() if close else m.end()


def _html_to_text(s: str, strip_quotes: bool = True) -> str:
    s = _drop_elements(_drop_comments(s))
    if strip_quotes:
        m = _HTML_QUOTE_START.search(s)
        if m:
            s = s[:m.start()]
        prev = None
        for _ in range(50):  # innermost first, so nested quotes go too
            if prev == s:
                break
            prev = s
            s = _BLOCKQUOTE_INNER.sub("\n", s)
        m = re.search(r"<blockquote\b", s, re.I)  # unclosed quote: drop the rest
        if m:
            s = s[:m.start()]
    s = _BLOCK_BREAK.sub("\n", s)
    s = _LIST_ITEM.sub("\n- ", s)
    s = _TAG.sub("", s)
    return html.unescape(s)


def _plain(raw: Any) -> str:
    """Raw body as plain text, quoted history kept (used to read bounce notices)."""
    s = str(raw or "")[:MAX_INPUT_CHARS].replace("\r\n", "\n").replace("\r", "\n")
    s = _html_to_text(s, strip_quotes=False) if _looks_like_html(s) else html.unescape(s)
    return s.replace("\xa0", " ")


def _next_nonempty(lines: Sequence[str], start: int) -> str:
    for ln in lines[start:]:
        if ln.strip():
            return ln.strip()
    return ""


def _cut_quoted(lines: List[str]) -> List[str]:
    out: List[str] = []
    n = len(lines)
    for i, line in enumerate(lines):
        st = line.strip()
        if st.startswith(">"):
            continue
        if line.rstrip() == "--":  # RFC 3676 signature delimiter ("-- ")
            break
        if st:
            if _QUOTE_HEADER_ON.match(st):
                break
            if _QUOTE_HEADER_BARE.match(st) and ("@" in st or re.search(r"\d", st)):
                break
            if _QUOTE_HEADER_START.match(st) and i + 1 < n:
                joined = f"{st} {lines[i + 1].strip()}"
                if len(joined) < 400 and _QUOTE_HEADER_ON.match(joined):
                    break
            if _ORIGINAL_MSG.match(st):
                break
            if _UNDERSCORE_SEP.match(st) and _HEADER_FROM.match(_next_nonempty(lines, i + 1)):
                break
            if _HEADER_FROM.match(st) and any(_HEADER_NEXT.match(x.strip()) for x in lines[i + 1:i + 5]):
                break
            if _MOBILE_SIG.match(st):
                break
        out.append(line)
    return out


def _cut_signoff(lines: List[str]) -> List[str]:
    """Drop a trailing "Thanks,\\nJane Doe\\nCFO ..." block (only when it really is one)."""
    idx = [i for i, ln in enumerate(lines) if ln.strip()]
    for pos in range(len(idx) - 1, max(0, len(idx) - 8), -1):
        i = idx[pos]
        m = _SIGNOFF.match(lines[i].strip())
        if not m:
            continue
        after = [lines[j].strip() for j in idx[pos + 1:]]
        if len(after) > 6 or any(len(a) > 80 for a in after):
            return lines
        if not after or m.group("name") or _NAMEISH_LINE.match(after[0]):
            return lines[:i]
        return lines
    return lines


def clean_reply_text(text_or_html: Any) -> str:
    """Return just what the person wrote, as one whitespace-collapsed line.

    Strips HTML tags/entities (and HTML-quoted history: ``<blockquote>``,
    Gmail/Outlook/Yahoo quote containers), drops quoted lines (``>``) and
    everything from the first quoted-history header on ("On <date> ...
    wrote:", "-----Original Message-----", Outlook "From: ... Sent: ..."
    blocks), the signature after a ``-- `` line, mobile footers ("Sent from
    my iPhone") and a trailing sign-off + signature block ("Best,\\nJane").
    """
    if text_or_html is None:
        return ""
    s = str(text_or_html)[:MAX_INPUT_CHARS].replace("\r\n", "\n").replace("\r", "\n")
    if not s.strip():
        return ""
    s = _html_to_text(s) if _looks_like_html(s) else html.unescape(s)
    s = s.replace("\xa0", " ").replace("​", "")
    lines = _cut_signoff(_cut_quoted(s.split("\n")))
    return _WS.sub(" ", " ".join(lines)).strip()


# =============================================================================
# Tokenised view of a reply (for negation-aware phrase matching)
# =============================================================================

_APOS = str.maketrans({"’": "'", "‘": "'", "`": "'", "´": "'"})
_CONTRACTIONS: List[Tuple[Pattern[str], str]] = [
    (re.compile(r"\bcan'?t\b"), "can not"),
    (re.compile(r"\bcannot\b"), "can not"),
    (re.compile(r"\bwon'?t\b"), "will not"),
    (re.compile(r"n't\b"), " not"),
    (re.compile(r"\b(do|does|did|is|are|was|were|would|could|should|have|has|had|need|must|ai)nt\b"),
     r"\1 not"),
    (re.compile(r"'re\b"), " are"),
    (re.compile(r"'m\b"), " am"),
    (re.compile(r"'ll\b"), " will"),
    (re.compile(r"'ve\b"), " have"),
]
_TOKEN = re.compile(r"[a-z0-9]+|[,.;:!?]")
_PUNCT = frozenset(",.;:!?")
_NEGATORS = frozenset({"not", "no", "never", "nothing", "none", "nor", "neither", "without",
                       "hardly", "barely"})
_SCOPE_BREAK = frozenset({"but", "though", "although", "however", "yet", "still"})


# Negation-shaped idioms that are not negations ("don't hesitate to send me times").
_IDIOMS: List[Tuple[Pattern[str], str]] = [
    (re.compile(r"\bdo not hesitate\b"), "feel free"),
    (re.compile(r"\b(?:no|not a) (?:problem|worries)\b"), "fine"),
]


def _prep(text: Any) -> str:
    """Lowercase, strip accents, unify apostrophes, expand negative contractions."""
    t = strip_accents(str(text or "")).lower().translate(_APOS)
    for pat, rep in _CONTRACTIONS:
        t = pat.sub(rep, t)
    for pat, rep in _IDIOMS:
        t = pat.sub(rep, t)
    return t


def _tokens(text: Any) -> List[str]:
    return _TOKEN.findall(_prep(text))


@lru_cache(maxsize=2048)
def _phrase_re(phrase: str) -> Optional[Pattern[str]]:
    words = [w for w in _tokens(phrase) if w not in _PUNCT]
    if not words:
        return None
    return re.compile(r"(?<= )" + r" (?:, )?".join(re.escape(w) for w in words) + r"(?= )")


class _Text:
    """Token view of a text: whole-word phrase search with negation awareness."""

    def __init__(self, text: Any):
        self.tokens = _tokens(text)
        self.joined = " " + " ".join(self.tokens) + " "

    def __bool__(self) -> bool:
        return bool(self.tokens)

    def _negated(self, idx: int, window: int = 3) -> bool:
        for j in range(idx - 1, max(-1, idx - 1 - window), -1):
            tok = self.tokens[j]
            if tok in _PUNCT or tok in _SCOPE_BREAK:
                return False
            if tok in _NEGATORS:
                return True
        return False

    def find(self, phrase: str, negatable: bool = False,
             unless_next: Iterable[str] = ()) -> bool:
        pat = _phrase_re(phrase)
        if pat is None:
            return False
        stop_next = set(unless_next)
        for m in pat.finditer(self.joined):
            start = self.joined.count(" ", 0, m.start()) - 1
            if negatable and self._negated(start):
                continue
            if stop_next:
                nxt = self.joined.count(" ", 0, m.end())
                if any(t in stop_next for t in self.tokens[nxt:nxt + 2]):
                    continue
            return True
        return False

    def first(self, phrases: Iterable[str], negatable: bool = False,
              unless_next: Iterable[str] = ()) -> Optional[str]:
        for p in phrases:
            if self.find(p, negatable=negatable, unless_next=unless_next):
                return p
        return None


# =============================================================================
# Dates
# =============================================================================

_MONTHS: Dict[str, int] = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4,
    "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9,
    "sept": 9, "sep": 9, "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12,
    "dec": 12,
}
_WEEKDAYS: Dict[str, int] = {
    "monday": 0, "mon": 0, "tuesday": 1, "tues": 1, "tue": 1, "wednesday": 2, "wed": 2,
    "thursday": 3, "thurs": 3, "thur": 3, "thu": 3, "friday": 4, "fri": 4, "saturday": 5,
    "sat": 5, "sunday": 6, "sun": 6,
}
_MON = "|".join(sorted(_MONTHS, key=len, reverse=True))
_WD = "|".join(sorted(_WEEKDAYS, key=len, reverse=True))
_ORD = r"(?:st|nd|rd|th)?"

_DATE_EXPR = re.compile(
    rf"(?:(?:{_WD})\.?,?\s+(?:the\s+)?)?"
    rf"(?:(?P<iy>\d{{4}})-(?P<im>\d{{1,2}})-(?P<id>\d{{1,2}})(?!\d)"
    rf"|(?P<mdm>{_MON})\.?\s+(?:the\s+)?(?P<mdd>\d{{1,2}}){_ORD}\b(?:,?\s+(?P<mdy>\d{{4}})\b)?"
    rf"|(?P<dmd>\d{{1,2}}){_ORD}\s+(?:of\s+)?(?P<dmm>{_MON})\b\.?(?:,?\s+(?P<dmy>\d{{4}})\b)?"
    rf"|(?P<n1>\d{{1,2}})(?P<sep>[/.-])(?P<n2>\d{{1,2}})(?:(?P=sep)(?P<n3>\d{{4}}|\d{{2}}))?(?![\d/.-]?\d))",
    re.I)
_MONTH_ONLY = re.compile(
    rf"(?:(?P<pos>early|mid|late|the\s+end\s+of|end\s+of|the\s+beginning\s+of|beginning\s+of"
    rf"|the\s+start\s+of|start\s+of)[\s-]+)?(?P<mo>{_MON})\b(?!\.?\s*\d)(?:,?\s+(?P<moy>\d{{4}})\b)?",
    re.I)
_WEEKDAY_EXPR = re.compile(rf"(?:(?:this|next|coming|this\s+coming)\s+)?(?P<wd>{_WD})\b\.?", re.I)
_DAY_ORD = re.compile(r"(?:the\s+)?(?P<dom>\d{1,2})(?:st|nd|rd|th)\b", re.I)
_RELATIVE_DAY = re.compile(r"(?P<rel>tomorrow|next\s+week)\b", re.I)
_DATE_FILLER = re.compile(
    r"\s*(?:[,:\-–—]\s*|(?:to\s+(?:the\s+)?(?:office|work|my\s+desk)|in\s+(?:the\s+)?(?:office|work)"
    r"|at\s+(?:my\s+desk|work)|online|on|in|at|from|by|the|and|again|office|work)\b\s*)", re.I)
_POS_DAY = {"early": 1, "beginning": 1, "start": 1, "mid": 15, "late": 25, "end": 25}


def _safe_date(y: int, m: int, d: int) -> Optional[date]:
    try:
        return date(y, m, d)
    except (ValueError, OverflowError):
        return None


def _next_occurrence(m: int, d: int, today: date, grace: int) -> Optional[date]:
    """First (month, day) on/after ``today - grace`` days, this year or the next ones."""
    floor = today - timedelta(days=max(0, grace))
    for y in (today.year, today.year + 1, today.year + 2, today.year + 3, today.year + 4):
        c = _safe_date(y, m, d)
        if c and c >= floor:
            return c
    return None


def _pick(cands: List[date], today: date, grace: int) -> Optional[date]:
    """Nearest candidate on/after ``today - grace``; else the latest one."""
    if not cands:
        return None
    floor = today - timedelta(days=max(0, grace))
    ok = [c for c in cands if c >= floor]
    return min(ok) if ok else max(cands)


def _ymd(y: Optional[str], m: int, d: int, today: date, grace: int) -> Optional[date]:
    if y:
        yy = int(y)
        return _safe_date(yy + 2000 if yy < 100 else yy, m, d)
    return _next_occurrence(m, d, today, grace)


def _parse_date_at(text: str, pos: int, today: date, grace: int = 0,
                   allow_weekday: bool = True) -> Optional[Tuple[date, int, str]]:
    """Parse a date expression starting exactly at ``pos``. Returns (date, end, kind)."""
    m = _DATE_EXPR.match(text, pos)
    if m:
        g = m.groupdict()
        d: Optional[date] = None
        if g["iy"]:
            d = _safe_date(int(g["iy"]), int(g["im"]), int(g["id"]))
        elif g["mdm"]:
            d = _ymd(g["mdy"], _MONTHS[g["mdm"].lower()], int(g["mdd"]), today, grace)
        elif g["dmm"]:
            d = _ymd(g["dmy"], _MONTHS[g["dmm"].lower()], int(g["dmd"]), today, grace)
        elif g["n1"] and (g["n3"] or g["sep"] == "/"):
            a, b = int(g["n1"]), int(g["n2"])
            # month/day or day/month: both readings, nearest plausible wins (country-agnostic)
            cands = [c for c in (_ymd(g["n3"], a, b, today, grace) if 1 <= a <= 12 else None,
                                 _ymd(g["n3"], b, a, today, grace) if 1 <= b <= 12 else None) if c]
            d = _pick(cands, today, grace)
        if d:
            return d, m.end(), "date"
    m = _MONTH_ONLY.match(text, pos)
    if m:
        mon = _MONTHS[m.group("mo").lower()]
        posw = (m.group("pos") or "").lower().split()
        key = next((w for w in posw if w in _POS_DAY), "")
        day = _POS_DAY.get(key, 1)
        if m.group("moy"):
            d = _safe_date(int(m.group("moy")), mon, day)
        else:
            d = _next_occurrence(mon, day, today, grace)
        if d:
            return d, m.end(), "month"
    if allow_weekday:
        m = _WEEKDAY_EXPR.match(text, pos)
        if m:
            wd = _WEEKDAYS[m.group("wd").lower()]
            ahead = (wd - today.weekday()) % 7 or 7
            return today + timedelta(days=ahead), m.end(), "weekday"
        m = _RELATIVE_DAY.match(text, pos)
        if m:
            if m.group("rel").lower() == "tomorrow":
                return today + timedelta(days=1), m.end(), "relative"
            return today + timedelta(days=7 - today.weekday()), m.end(), "relative"
    m = _DAY_ORD.match(text, pos)
    if m:  # a bare "the 2nd" always means the next one (no grace window)
        dom = int(m.group("dom"))
        y, mo = today.year, today.month
        for _ in range(13):
            c = _safe_date(y, mo, dom)
            if c and c >= today:
                return c, m.end(), "day"
            mo += 1
            if mo > 12:
                y, mo = y + 1, 1
    return None


def _skip_fillers(text: str, pos: int) -> int:
    for _ in range(8):
        m = _DATE_FILLER.match(text, pos)
        if not m or m.end() == pos:
            break
        pos = m.end()
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def _dates_after(trigger: Pattern[str], text: str, today: date, grace: int
                 ) -> List[Tuple[int, date, str, str]]:
    """(position, date, kind, trigger word) for each date right after a trigger."""
    out = []
    for m in trigger.finditer(text):
        at = _skip_fillers(text, m.end())
        got = _parse_date_at(text, at, today, grace)
        if got:
            out.append((m.start(), got[0], got[2], m.group(0).lower()))
    return out


def _scan_unambiguous_dates(text: str, today: date, grace: int) -> List[date]:
    """Dates anywhere in the text that need no context (month names, ISO, d/m/yyyy)."""
    out = []
    for m in _DATE_EXPR.finditer(text):
        if m.group("n1") and not m.group("n3"):
            continue  # "24/7", "3/4" ... too ambiguous without a trigger word
        got = _parse_date_at(text, m.start(), today, grace, allow_weekday=False)
        if got:
            out.append(got[0])
    return out


_OOO_DATE_TRIGGER = re.compile(
    r"\b(?:back|return|returns|returning|returned|until|till|til|through|thru|resume|resuming"
    r"|reachable|available)\b", re.I)
# "back"/"returning" + a date = someone away; but "check back in May", "get back to you" are not.
_OOO_BACK_TRIGGER = re.compile(
    r"(?<!check )(?<!get )(?<!getting )(?<!come )(?<!circle )(?<!reach )(?<!write )(?<!call )"
    r"(?<!hear )(?<!ping )(?<!report )(?<!loop )(?<!send )(?<!sent )(?<!give )(?<!bring )(?<!reply )"
    r"(?<!pay )(?<!push )(?<!pushed )(?<!move )(?<!moved )(?<!it )(?<!this )(?<!that )(?<!them )"
    r"\bback\b|\breturn(?:s|ing)?\b", re.I)


def ooo_return_date(text: str, today: date) -> Optional[date]:
    """Return date in an out-of-office text ("back on October 3", "until Monday", ...).

    Dates without a year resolve to the next occurrence (a date up to 30 days
    in the past is kept as-is: a stale auto-reply means they are already back).
    """
    low = _prep(text)
    found = [d for _, d, _, _ in _dates_after(_OOO_DATE_TRIGGER, low, today, grace=30)]
    if found:
        return max(found)
    loose = _scan_unambiguous_dates(low, today, grace=30)
    return max(loose) if loose else None


# --- timing ("not now, try later") ---------------------------------------------

_NUM_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
              "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
              "couple": 2, "few": 3, "several": 4}
_NUM = (r"\d{1,3}|a\s+couple(?:\s+of)?|couple(?:\s+of)?|a\s+few|few|several|an|a|one|two|three|four"
        r"|five|six|seven|eight|nine|ten|eleven|twelve")
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}


def _num(s: str) -> int:
    s = s.strip().lower()
    if s.isdigit():
        return int(s)
    for w in reversed(s.split()):
        if w in _NUM_WORDS:
            return _NUM_WORDS[w]
    return 1


def _quarter_start(q: int, today: date, year: Optional[int] = None) -> date:
    d = date(year or today.year, 3 * (q - 1) + 1, 1)
    if year is None and d <= today:
        d = date(today.year + 1, d.month, 1)
    return d


def _next_quarter_start(today: date) -> date:
    q = (today.month - 1) // 3 + 1
    return date(today.year + 1, 1, 1) if q == 4 else date(today.year, 3 * q + 1, 1)


def _later_this_year(today: date) -> date:
    cap = date(today.year, 12, 1)
    target = today + timedelta(days=60)
    return min(target, cap) if cap > today + timedelta(days=7) else today + timedelta(days=DEFAULT_TIMING_DAYS)


def _q_year(m: "re.Match[str]", idx: int) -> Optional[int]:
    y = m.group(idx)
    return int(y) if y else None


_PERIODS: List[Tuple[Pattern[str], Callable[["re.Match[str]", date], date]]] = [
    (re.compile(r"\bnext\s+quarter\b"), lambda m, t: _next_quarter_start(t)),
    (re.compile(r"\bnext\s+month\b"), lambda m, t: t + timedelta(days=30)),
    (re.compile(r"\bnext\s+week\b"), lambda m, t: t + timedelta(days=7)),
    (re.compile(r"\b(?:early\s+)?next\s+year\b|\b(?:in\s+)?the\s+new\s+year\b|\bnew\s+year\b"),
     lambda m, t: date(t.year + 1, 1, 1)),
    (re.compile(r"\blater\s+(?:this|in\s+the)\s+year\b|\b(?:towards?\s+the\s+)?end\s+of\s+(?:the|this)\s+year\b"),
     lambda m, t: _later_this_year(t)),
    (re.compile(r"\b(?:after|post|following)\s+(?:the\s+)?q([1-4])(?:\s+(20\d\d))?\b"),
     lambda m, t: (date(_q_year(m, 2) + (1 if int(m.group(1)) == 4 else 0), (int(m.group(1)) % 4) * 3 + 1, 1)
                   if _q_year(m, 2) else _quarter_start(int(m.group(1)) % 4 + 1, t))),
    (re.compile(r"\b(?:in|during|for|by|until|till|before|early|mid|late|start\s+of|beginning\s+of"
                r"|end\s+of|around)\s+(?:the\s+)?q([1-4])(?:\s+(20\d\d))?\b"),
     lambda m, t: _quarter_start(int(m.group(1)), t, _q_year(m, 2))),
    (re.compile(r"\bq([1-4])\s+(?:next\s+year|(20\d\d))\b"),
     lambda m, t: (_quarter_start(int(m.group(1)), t, _q_year(m, 2)) if _q_year(m, 2)
                   else date(t.year + 1, 3 * (int(m.group(1)) - 1) + 1, 1))),
    (re.compile(rf"\bin\s+(?:about\s+|around\s+|another\s+|roughly\s+)?({_NUM})\s+(day|week|month|year)s?\b"),
     lambda m, t: t + timedelta(days=_num(m.group(1)) * _UNIT_DAYS[m.group(2)])),
    (re.compile(rf"\b({_NUM})\s+(day|week|month|year)s?(?:'|’)?\s+(?:from\s+now|time)\b"),
     lambda m, t: t + timedelta(days=_num(m.group(1)) * _UNIT_DAYS[m.group(2)])),
]
_TIMING_DATE_TRIGGER = re.compile(
    r"\b(?:after|from|in|until|till|around|on|by|about|circa|once)\b"
    # zero-width: "early/mid/late/end of <Month>" keeps its position word for the parser
    rf"|(?=\b(?:early|mid|late|the\s+end\s+of|end\s+of|the\s+beginning\s+of|beginning\s+of"
    rf"|the\s+start\s+of|start\s+of)[\s-]+(?:{_MON})\b)", re.I)


def timing_follow_up(text: str, today: date, default_days: int = DEFAULT_TIMING_DAYS
                     ) -> Tuple[date, bool]:
    """Re-contact date for a "not now" reply -> (date, explicit).

    Period phrases first (next month -> +30d, next quarter -> start of next
    quarter, "in N days/weeks/months", "after Q1", "in the new year", ...),
    then an explicit date after a time word ("after October 15", "in
    January", "early November"), else today + ``default_days`` (explicit=False).
    """
    low = _prep(text)
    hits: List[Tuple[int, date]] = []
    for pat, fn in _PERIODS:
        for m in pat.finditer(low):
            try:
                hits.append((m.start(), fn(m, today)))
            except (ValueError, OverflowError):
                continue
    future = [(p, d) for p, d in hits if d > today]
    if future:
        return min(future)[1], True
    for _, d, kind, word in sorted(_dates_after(_TIMING_DATE_TRIGGER, low, today, grace=0)):
        if word == "after":
            if kind == "month":
                d = date(d.year + (1 if d.month == 12 else 0), d.month % 12 + 1, 1)
            else:
                d = d + timedelta(days=1)
        if d > today:
            return d, True
    return today + timedelta(days=max(1, int(default_days or DEFAULT_TIMING_DAYS))), False


# =============================================================================
# Keyword rules
# =============================================================================

_DAEMON_LOCALS = ("mailer-daemon", "mailer_daemon", "mailerdaemon", "mail-daemon", "maildaemon",
                  "mail_daemon", "postmaster", "microsoftexchange", "mail.delivery", "maildelivery")
_BOUNCE_PHRASES = (
    "delivery status notification", "undeliverable", "undelivered mail", "address not found",
    "recipient address rejected", "delivery has failed", "mail delivery failed",
    "mail delivery failure", "delivery failure", "message not delivered", "could not be delivered",
    "can not be delivered", "was not delivered", "user unknown", "no such user",
    "mailbox unavailable", "mailbox not found", "recipient not found", "recipnotfound",
    "returned mail", "failure notice", "delivery to the following recipient failed",
    "the email account that you tried to reach does not exist", "address rejected",
)
_DELAY_PHRASES = (
    "delivery status notification delay", "delivery delayed", "delayed mail", "message delayed",
    "delivery incomplete", "will retry", "will continue to retry", "will be retried",
    "not yet been delivered", "still undelivered", "still trying to deliver",
)
_OOO_STRONG = (
    "out of office", "out of the office", "automatic reply", "auto reply", "autoreply",
    "auto response", "automatic response", "autoresponder", "auto responder", "on annual leave",
    "on vacation", "on holiday", "on holidays", "on leave", "on sabbatical", "away until",
    "currently away", "currently out of", "away from the office",
    "away from my desk", "limited access to email", "limited access to my email",
    "limited access to e mail", "limited email access", "no access to email",
    "no access to my email", "intermittent access", "ooo",
)
# Checked only in the subject part *before* any "Re:"/"Fwd:" (our own subject line
# may contain anything, e.g. "Holiday hiring push").
_OOO_SUBJECT = ("automatic reply", "auto reply", "autoreply", "auto response", "automatic response",
                "autoresponse", "autoresponder", "out of office", "out of the office", "ooo",
                "away from the office", "on leave", "annual leave", "on vacation", "on holiday",
                "absent", "absence", "abwesend", "abwesenheit")
_SUBJECT_PREFIX = re.compile(r"\b(?:re|fw|fwd|aw|sv|wg|tr|rv|antw|odp|vs)\s*:", re.I)
_BARE_STOP = re.compile(r"^\W*(?:stop|remove|remove me|unsubscribe|opt out|opt-out)\W*$", re.I)
_OOO_LEAVE = re.compile(r"\bon\s+(?:[a-z]+\s+){0,2}leave\b"
                        r"|\b(?:am|be|is|are|currently)\s+(?:out|off|away)\s+until\b")
_UNSUBSCRIBE = (
    "unsubscribe", "unsub", "remove me", "remove us", "remove my email", "remove my address",
    "remove this email", "take me off", "take us off", "stop emailing", "stop sending",
    "stop contacting", "stop spamming", "stop messaging", "do not contact", "do not email",
    "do not message", "do not reach out", "do not write", "opt out", "opt me out", "no more emails",
    "no further emails", "no more contact", "never contact", "never email", "leave me alone",
    "off your list", "off your mailing list", "this is spam",
)
_NEG_HARD = (
    "not a fit", "not a good fit", "not the right fit", "not a match", "please do not",
    "we use an in house", "we have an in house", "we do this in house", "we do it in house",
    "handle this in house", "handled in house", "not relevant", "already have someone",
    "already have a provider", "already have an agency", "already have a partner",
    "already have a supplier", "already have a vendor", "already work with someone",
    "already covered",
)
_NEG_SOFT = (
    "not interested", "no thanks", "no thank you", "we are all set", "we are good", "no interest",
    "will pass", "going to pass", "not looking", "no need", "not for us", "do not need",
    "does not need", "not something we", "not what we",
)
_NEG_INTEREST = re.compile(r"\b(?:not|never|no\s+longer|nor)\s+(?:[a-z]+\s+){0,4}interested\b")
_BARE_NO = re.compile(r"^\W*(?:no|nope|nah|no thanks|no thank you)\W*$", re.I)
_TIMING_PHRASES = (
    "not right now", "not now", "not at the moment", "not at this time", "not at this stage",
    "not at this point", "not yet", "maybe later", "not the right time", "bad timing", "bad time",
    "too early", "too busy", "next quarter", "next month", "in a few months",
    "in a couple of months", "in a couple months", "in a few weeks", "in a couple of weeks",
    "reach out in", "reach out again", "check back", "check in again", "try again", "try us again",
    "try me again", "get back in touch", "follow up in", "circle back", "touch base in",
    "revisit", "reconnect", "later this year", "early next year", "in the new year",
)
_TIMING_RE = re.compile(
    r"\b(?:after|post|in|during|until|till|before|early|mid|late|start\s+of|beginning\s+of|end\s+of)"
    r"\s+(?:the\s+)?q[1-4]\b|\bq[1-4]\s+(?:next\s+year|20\d\d)\b"
    r"|\b(?:reach\s+out|get\s+in\s+touch|contact\s+(?:me|us)|follow\s+up|circle\s+back|check\s+(?:back|in)"
    r"|touch\s+base|try\s+(?:me|us|again)|ping\s+(?:me|us)|get\s+back\s+to\s+(?:me|us)|revisit|reconnect)"
    rf"\b[^.!?]{{0,25}}?\b(?:next\s+(?:month|quarter|year)|in\s+(?:{_NUM})\s+(?:weeks?|months?)"
    rf"|in\s+(?:{_MON})|after\s+(?:{_MON}))\b")
_POSITIVE_STRONG = (
    "interested", "sounds good", "sounds great", "sounds interesting", "let's chat", "let's talk",
    "let's connect", "let's do it", "let's set up", "let's schedule", "let's book",
    "happy to chat", "happy to talk", "happy to connect", "happy to hop on", "happy to jump on",
    "send over", "send me", "send it over", "send through", "tell me more", "learn more",
    "book a", "book in", "calendar", "calendly", "what times", "what time", "free on",
    "available on", "open to", "keen", "love to", "would like to", "set up a call",
    "set up a meeting", "schedule a call", "jump on a call", "hop on a call", "call me",
    "give me a call", "more info", "more information", "more details", "go ahead",
)
_POSITIVE_WEAK = ("yes", "sure", "definitely", "absolutely", "ok", "okay", "yep", "yeah")
_QUESTION_PHRASES = ("how much", "pricing", "price", "cost", "costs", "how does", "how do you",
                     "can you explain", "could you explain", "what do you charge", "rates", "fees")
_URL = re.compile(r"https?://\S+|www\.\S+", re.I)

# --- referral extraction ---------------------------------------------------------------

_REF_AFTER = re.compile(
    r"\b(?:speak|talk)(?:ing)?\s+(?:to|with)\b|\breach(?:ing)?\s+out\s+to\b|\bget(?:ting)?\s+in\s+touch\s+with\b"
    r"|\bconnect\s+with\b|\bforward(?:ing|ed)?\s+(?:this|it|your\s+(?:email|message|note|mail))?\s*(?:on\s+)?to\b"
    r"|\bpass(?:ing|ed)?\s+(?:this|it|your\s+(?:email|message|note))\s+(?:on\s+|along\s+)?to\b"
    r"|\b(?:better|right|best|correct|appropriate)\s+(?:person|contact|people)\b(?:\s+(?:to|for)\s+\w+"
    r"(?:\s+(?:to|with))?)?(?:\s+(?:here|would\s+be|will\s+be|is))?"
    r"|\bthe\s+person\s+you\s+(?:want|need)(?:\s+is)?\b"
    r"|\b(?:handled|managed|owned|looked\s+after|taken\s+care\s+of)\s+by\b", re.I)
_REF_IMMEDIATE = re.compile(
    r"\b(?:contact|email|e-mail|ping|try|ask|cc['’]?(?:ing|ed|d)|copying(?:\s+in)?|copied(?:\s+in)?"
    r"|loop(?:ing|ed)?\s+in|adding|introducing)\b", re.I)
_REF_BEFORE = re.compile(
    r"\b(?:handles|manages|owns|runs|covers)\s+(?:this|that|these|those|our|all|it|the)\b"
    r"|\b(?:is\s+)?in\s+charge\s+of\b|\b(?:is\s+)?responsible\s+for\b|\blooks\s+after\b"
    r"|\bdeals\s+with\b|\btakes\s+care\s+of\b"
    r"|\bis\s+(?:the|a)\s+(?:better|right|best|correct)\s+(?:person|contact)\b"
    r"|\b(?:is|would\s+be)\s+better\s+placed\b", re.I)
_REF_WEAK_CUES = ("colleague", "colleagues", "instead", "rather", "better person", "right person",
                  "directly", "in charge", "responsible", "handles", "contact", "reach out", "speak",
                  "talk", "forward", "forwarding", "cc", "loop", "copy", "copying", "try")
_WORD = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’\-]*")
_NOT_NAMES = frozenset("""
i i'm im the our my your we he she they it its this that these those please thanks thank hi hello
hey dear best regards cheers mr mrs ms miss dr prof sir madam team head director manager department
finance financial marketing sales operations ops procurement purchasing accounts accounting office
support recruitment recruiting talent people admin administration legal engineering product growth
partnerships business development chief officer executive president founder cofounder owner lead
senior junior global regional general vice human resources customer success service services
linkedin email e-mail re fw fwd unfortunately however also yes no sure sorry not but and or so as if
hr it he's she's they're we're you're let's our's someone somebody anyone anybody everyone company
monday tuesday wednesday thursday friday saturday sunday january february march april may june july
august september october november december ceo cfo coo cto cmo cro cpo vp svp evp md gm
""".split())
_STOP_SCAN = frozenset("""
me us him her them you someone somebody anyone anybody everyone it this that when if once after
before about regarding next again later instead soon tomorrow today there here who whoever
""".split())


def _is_name_token(w: str) -> bool:
    if len(w) < 2 or w.lower().strip("'’") in _NOT_NAMES:
        return False
    if not w[0].isupper():
        return False
    if w[1].islower():
        return True
    return len(w) > 2 and w[1] in "'’" and w[2].isupper()  # O'Neil


def _sentence_after(text: str, pos: int, limit: int = 90) -> str:
    seg = text[pos:pos + limit]
    m = re.search(r"[.!?;](?:\s|$)|\n", seg)
    return seg[:m.start()] if m else seg


def _sentence_before(text: str, pos: int, limit: int = 90) -> str:
    seg = text[max(0, pos - limit):pos]
    cuts = [m.end() for m in re.finditer(r"[.!?;](?:\s|$)|\n", seg)]
    return seg[cuts[-1]:] if cuts else seg


def _strip_possessive(w: str) -> Tuple[str, bool]:
    for suffix in ("'s", "’s"):
        if w.endswith(suffix) and len(w) > 3:
            return w[:-2], True
    return w, False


def _name_after(window: str, max_skip: int) -> str:
    run: List[str] = []
    skipped = 0
    for m in _WORD.finditer(window):
        w, possessive = _strip_possessive(m.group(0))
        if possessive and _is_name_token(w):
            run.append(w)
            break  # "Jane's team": the name ends here
        if "@" in window[m.end():m.end() + 1]:
            if run:
                break
            continue
        if _is_name_token(w):
            run.append(w)
            if len(run) == 3:
                break
            continue
        if run:
            break
        if w.lower() in _STOP_SCAN:
            return ""
        skipped += 1
        if skipped > max_skip:
            return ""
    return " ".join(run)


def _name_before(window: str) -> str:
    runs: List[List[str]] = []
    cur: List[str] = []
    for m in _WORD.finditer(window):
        w = _strip_possessive(m.group(0))[0]
        if "@" in window[m.end():m.end() + 1] or (m.start() > 0 and window[m.start() - 1] in "@."):
            if cur:
                runs.append(cur)
                cur = []
            continue
        if _is_name_token(w):
            cur.append(w)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return " ".join(runs[-1][:3]) if runs else ""


def name_from_email(email: str) -> str:
    """'jane.smith@acme.com' -> 'Jane Smith' (only for first.last-style addresses)."""
    local = (email or "").split("@", 1)[0].lower()
    m = re.fullmatch(r"([a-z]{2,})[._-]([a-z]{2,})", local)
    if not m or m.group(1) in _NOT_NAMES:
        return ""
    return f"{m.group(1).capitalize()} {m.group(2).capitalize()}"


def _own_domain(ctx: Any) -> str:
    try:
        return normalize_domain(ctx.playbook.offer.get("sender_website") or "")
    except AttributeError:
        return ""


def _is_daemon(email: str) -> bool:
    local = (email or "").split("@", 1)[0].lower()
    return bool(local) and local.startswith(_DAEMON_LOCALS)


def _is_machine_address(email: str) -> bool:
    local = (email or "").split("@", 1)[0].lower()
    return _is_daemon(email) or local.startswith(("noreply", "no-reply", "no_reply", "donotreply",
                                                  "do-not-reply", "bounce"))


def _third_party_emails(text: str, reply: Reply, ctx: Any) -> List[Tuple[int, str]]:
    own = _own_domain(ctx)
    out: List[Tuple[int, str]] = []
    seen = set()
    for m in _EMAIL_RE.finditer(text):
        e = m.group(0).strip(".").lower()
        if e in seen or e == reply.from_email or not is_valid_email(e) or _is_machine_address(e):
            continue
        if own and normalize_domain(e) == own:
            continue
        seen.add(e)
        out.append((m.start(), e))
    return out


def _name_next_to_email(text: str, pos: int) -> str:
    """'Jane Smith (jane@x.com)' / 'Jane Smith <jane@x.com>' / 'Jane Smith - jane@x.com'."""
    before = text[max(0, pos - 60):pos]
    m = re.search(r"([A-Z][\w'’\-]+(?:\s+[A-Z][\w'’\-]+){0,2})\s*(?:[(<\[]|[-–—:,]|\bat\b|\bon\b|\bvia\b)?\s*<?$",
                  before)
    if not m:
        return ""
    words = [w for w in m.group(1).split() if _is_name_token(w)]
    if words and len(words) == len(m.group(1).split()):
        return " ".join(words)
    # keep the trailing run of real name tokens ("Speak to Jane Smith" -> "Jane Smith")
    tail: List[str] = []
    for w in reversed(m.group(1).split()):
        if not _is_name_token(w):
            break
        tail.insert(0, w)
    return " ".join(tail)


def extract_referral(text: str, reply: Reply, ctx: Any) -> Optional[Tuple[str, str]]:
    """(name, email) of the person the reply points us to, or None.

    Needs a referral cue ("speak to", "reach out to", "contact <Name>",
    "better person", "<Name> handles this", "in charge of", "cc'ing",
    "forwarding to", ...) plus a capitalised name or a third-party address
    (never the sender's own, ours or a no-reply/daemon one).
    """
    if not text:
        return None
    emails = _third_party_emails(text, reply, ctx)
    names: List[Tuple[int, str]] = []
    trigger_positions: List[int] = []
    for m in _REF_AFTER.finditer(text):
        trigger_positions.append(m.end())
        n = _name_after(_sentence_after(text, m.end()), max_skip=5)
        if n:
            names.append((m.end(), n))
    for m in _REF_IMMEDIATE.finditer(text):
        trigger_positions.append(m.end())
        n = _name_after(_sentence_after(text, m.end()), max_skip=0)
        if n:
            names.append((m.end(), n))
    for m in _REF_BEFORE.finditer(text):
        trigger_positions.append(m.start())
        n = _name_before(_sentence_before(text, m.start()))
        if n:
            names.append((m.start(), n))
    has_trigger = bool(trigger_positions)
    if not has_trigger and emails and _Text(text).first(_REF_WEAK_CUES):
        has_trigger = True
    if not has_trigger or not (emails or names):
        return None
    email = ""
    if emails:
        if trigger_positions:
            first_trigger = min(trigger_positions)
            after = [e for p, e in emails if p >= first_trigger - 80]
            email = after[0] if after else emails[0][1]
        else:
            email = emails[0][1]
    name = ""
    if email:
        pos = next(p for p, e in emails if e == email)
        name = _name_next_to_email(text, pos)
    if not name and names:
        name = sorted(names)[0][1]
    if not name and email:
        name = name_from_email(email)
    return name, email


def extract_bounced_emails(text: str, exclude: Iterable[str] = (), own_domain: str = "") -> List[str]:
    """Candidate bounced (original recipient) addresses in a delivery-failure notice, best first.

    Reads DSN headers (Final-/Original-Recipient, X-Failed-Recipients), the
    Gmail/Outlook/Postfix/Exchange wording ("wasn't delivered to x@y",
    "Delivery has failed to these recipients", "<x@y>: host ... said"), the
    ``To:`` line of the returned message, then any other address. Excludes
    ``exclude``, addresses on ``own_domain``, daemon/no-reply addresses and
    anything on a ``From:`` line (that is us).
    """
    s = _plain(text)
    em = f"({_EMAIL_PAT})"
    patterns = [
        re.compile(rf"(?:final|original)-recipient:[ \t]*(?:rfc822[ \t]*;[ \t]*)?<?{em}", re.I),
        re.compile(rf"x-failed-recipients:[ \t]*<?{em}", re.I),
        re.compile(rf"\b(?:deliver\w*|recipients?|address(?:es)?|mailbox|user)\b[^@\n]{{0,80}}?<?{em}", re.I),
        re.compile(rf"^[ \t]*<{em}>[ \t]*:", re.I | re.M),
        re.compile(rf"^[ \t]*\*?to:\*?[ \t]*(?:[^<\n@]*<)?{em}", re.I | re.M),
        re.compile(em),
    ]
    from_line = {m.group(1).lower()
                 for m in re.finditer(rf"^[ \t]*\*?from:\*?[^\n]*?{em}", s, re.I | re.M)}
    skip = {e.strip().lower() for e in exclude if e} | from_line
    out: List[str] = []
    for pat in patterns:
        for m in pat.finditer(s):
            e = m.group(1).strip(".").lower()
            if e in skip or e in out or not is_valid_email(e) or _is_machine_address(e):
                continue
            if own_domain and normalize_domain(e) == own_domain:
                continue
            out.append(e)
    return out


# =============================================================================
# classify_rules
# =============================================================================

def _today(ctx: Any) -> date:
    return getattr(ctx, "today", None) or date.today()


def _cfg_days(ctx: Any, key: str, default: int) -> int:
    """A positive day count from ``playbook.replies[key]`` (bad values -> default)."""
    raw = ctx.playbook.replies.get(key)
    try:
        days = int(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        ctx.log.warning("replies.%s must be a whole number of days, got %r; using %d", key, raw, default)
        days = default
    return days if days > 0 else default


def _subject_head(subject: str) -> str:
    """The part of a subject added by the replying system: 'Automatic reply: Re: X' -> 'Automatic reply:'."""
    m = _SUBJECT_PREFIX.search(subject or "")
    return (subject or "")[:m.start()] if m else (subject or "")


def _reset(reply: Reply) -> None:
    reply.category, reply.confidence, reply.summary = RC.OTHER, 0.0, ""
    reply.referral_name = reply.referral_email = ""
    reply.follow_up_date = None
    reply.suggested_reply = ""


def _snippet(text: str, n: int = 100) -> str:
    return truncate(text, n) if text else ""


def _summ(label: str, text: str) -> str:
    snip = _snippet(text)
    return f"{label}: \"{snip}\"" if snip else label


def _fmt_date(d: date, today: date) -> str:
    s = f"{d.strftime('%B')} {d.day}"
    return s if d.year == today.year else f"{s}, {d.year}"


def classify_rules(reply: Reply, ctx: Any) -> Reply:
    """Classify with keyword/regex rules (see module docstring). Mutates + returns ``reply``."""
    today = _today(ctx)
    raw = reply.body or ""
    text = clean_reply_text(raw)
    subject = str(reply.subject or "")
    t, subj = _Text(text), _Text(_subject_head(subject))
    _reset(reply)
    reply.classifier = "rules"

    # 1. bounce / delivery notices ------------------------------------------------------
    # A person *mentioning* "undeliverable" is not a bounce: it takes a mail-server
    # sender, a delivery-failure subject, or failure wording from a no-reply address.
    daemon = _is_daemon(reply.from_email)
    subj_bounce = subj.first(_BOUNCE_PHRASES)
    machine = daemon or not reply.from_email or _is_machine_address(reply.from_email)
    if daemon or subj_bounce or (machine and t.first(_BOUNCE_PHRASES)):
        if subj.first(_DELAY_PHRASES) or t.first(_DELAY_PHRASES) or (
                daemon and _Text(_plain(raw)[:3000]).first(_DELAY_PHRASES) and not subj_bounce):
            reply.category, reply.confidence = RC.OTHER, 0.8
            reply.summary = "Delivery delayed (temporary) - the mail server will retry; no action"
            return reply
        source = _plain(raw) + "\n" + subject if (daemon or subj_bounce) else text
        cands = extract_bounced_emails(source, exclude=[reply.from_email], own_domain=_own_domain(ctx))
        reply.category = RC.BOUNCE
        reply.confidence = 0.95 if daemon else (0.9 if subj_bounce else 0.8)
        reply.summary = f"Bounced: {cands[0]}" if cands else "Delivery failure notice"
        return reply

    # 2. out of office -----------------------------------------------------------------------
    low = _prep(text)
    strong = subj.first(_OOO_SUBJECT) or t.first(_OOO_STRONG) or _OOO_LEAVE.search(low)
    dated = bool(_dates_after(_OOO_BACK_TRIGGER, low, today, grace=30))
    if strong or dated:
        ret = ooo_return_date(text, today) or ooo_return_date(subject, today)
        reply.category = RC.OOO
        reply.confidence = 0.9 if strong else 0.6
        reply.follow_up_date = ret.isoformat() if ret else None
        reply.summary = (f"Out of office until {_fmt_date(ret, today)}" if ret
                         else "Out of office (no return date given)")
        return reply

    if not t and not subj:
        reply.summary = "Empty reply"
        return reply

    # 3. unsubscribe ---------------------------------------------------------------------------
    guard = ("until", "till", "before", "for")
    if (t.first(_UNSUBSCRIBE, negatable=True, unless_next=guard) or _BARE_STOP.match(text)
            or subj.first(("unsubscribe", "remove me"))):
        reply.category, reply.confidence = RC.UNSUBSCRIBE, 0.9
        reply.summary = _summ("Asked to be removed", text)
        return reply

    # 4. referral ----------------------------------------------------------------------------------
    ref = extract_referral(text, reply, ctx)
    if ref:
        name, email = ref
        reply.category = RC.REFERRAL
        reply.confidence = 0.85 if email else 0.7
        reply.referral_name, reply.referral_email = name, email
        who = " ".join(x for x in (name, f"<{email}>" if email else "") if x)
        reply.summary = f"Referred us to {who}"
        return reply

    # 5/6. negative vs timing -------------------------------------------------------------------------
    default_days = _cfg_days(ctx, "timing_default_days", DEFAULT_TIMING_DAYS)
    hard = t.first(_NEG_HARD, unless_next=("hesitate", "worry"))
    soft = (t.first(_NEG_SOFT, unless_next=("to",)) or (_NEG_INTEREST.search(low) and "not interested")
            or (_BARE_NO.match(text) and "no"))
    timing_hit = t.first(_TIMING_PHRASES, negatable=True) or _TIMING_RE.search(low)
    when, explicit = timing_follow_up(text, today, default_days)
    if hard or (soft and not (timing_hit and explicit)):
        reply.category, reply.confidence = RC.NEGATIVE, 0.85 if hard else 0.8
        reply.summary = _summ("Not interested", text)
        return reply
    if timing_hit:
        reply.category, reply.confidence = RC.TIMING, 0.75
        reply.follow_up_date = when.isoformat()
        reply.summary = _summ(f"Not now - follow up around {_fmt_date(when, today)}", text)
        return reply

    # 7. positive ----------------------------------------------------------------------------------------
    pos = t.first(_POSITIVE_STRONG, negatable=True)
    weak = None if pos else t.first(_POSITIVE_WEAK, negatable=True)
    if pos or weak:
        reply.category, reply.confidence = RC.POSITIVE, 0.75 if pos else 0.55
        reply.summary = _summ("Interested", text)
        return reply

    # 8. question -------------------------------------------------------------------------------------------
    if "?" in _URL.sub(" ", text) or t.first(_QUESTION_PHRASES):
        reply.category, reply.confidence = RC.QUESTION, 0.6
        reply.summary = _summ("Asked a question", text)
        return reply

    reply.category, reply.confidence = RC.OTHER, 0.3
    reply.summary = _summ("Unclassified reply", text)
    return reply


# =============================================================================
# classify_ai
# =============================================================================

AI_SYSTEM_PROMPT = """You triage replies to B2B cold outreach emails. Read the prospect's reply and return ONLY a JSON object - no prose, no code fences.

Pick exactly one category:
- "positive": wants to move forward - agrees to a call or meeting, asks for times or a booking link, says they are interested, asks us to send details so they can evaluate.
- "question": asks something before deciding (price, how it works, who we are, results) without clearly agreeing to a next step.
- "referral": points us to someone else - names another person and/or gives their email address ("speak to Jane", "cc'ing our CFO", "forwarding this to the right person").
- "timing": not now but maybe later - asks us to come back at a later time (next month, next quarter, after an event) or says the timing is wrong.
- "negative": declines - not interested, no need, not a fit, already covered - without asking to be removed.
- "unsubscribe": asks to be removed, to stop emailing, to opt out or not to be contacted again.
- "ooo": an automatic out-of-office / away / on-leave message.
- "bounce": an automatic delivery-failure notice (address not found, undeliverable, mailbox does not exist).
- "other": anything else (automatic acknowledgements, unrelated or unclear messages).

Rules:
- Negations win: "not interested" is negative, never positive; "not right now" is timing.
- A request to stop contacting beats every other signal: that is "unsubscribe".
- If they decline but invite contact at a specific later time, use "timing".
- follow_up_date: for "timing" the date to get back in touch, for "ooo" the date they return. Resolve relative dates ("next quarter", "Monday", "in 3 weeks") against today's date given below. Format YYYY-MM-DD, or null when no date can be inferred. Use null for every other category.
- referral_name / referral_email: the person they pointed us to (only for "referral", otherwise empty strings). Never invent an email address.
- summary: one short sentence (max 20 words) saying what they said or want.
- suggested_reply: for positive, question, referral and timing, a short natural reply (under 80 words) written as the sender, plain text, no subject line, in the language of the reply. For "referral" address it to the referred person and mention who referred us. Never invent facts, prices or availability - use a placeholder like [answer] instead. Empty string for every other category.
- confidence: your confidence in the category, from 0.0 to 1.0.

Return exactly this shape:
{"category": "...", "confidence": 0.0, "summary": "...", "referral_name": "", "referral_email": "", "follow_up_date": null, "suggested_reply": ""}"""

_CATEGORY_ALIASES = {
    "interested": RC.POSITIVE, "meeting": RC.POSITIVE, "meeting_request": RC.POSITIVE,
    "not_interested": RC.NEGATIVE, "not interested": RC.NEGATIVE, "declined": RC.NEGATIVE,
    "out_of_office": RC.OOO, "out of office": RC.OOO, "out-of-office": RC.OOO, "auto_reply": RC.OOO,
    "autoreply": RC.OOO, "wrong_person": RC.REFERRAL, "wrong person": RC.REFERRAL,
    "not_now": RC.TIMING, "not now": RC.TIMING, "later": RC.TIMING, "unsub": RC.UNSUBSCRIBE,
    "opt_out": RC.UNSUBSCRIBE, "remove": RC.UNSUBSCRIBE, "bounced": RC.BOUNCE,
    "delivery_failure": RC.BOUNCE,
}
_NULLISH = {"", "null", "none", "n/a", "na", "-", "unknown"}


def _llm(ctx: Any) -> Any:
    """The configured LLM client, or None (dry run, not configured, or broken)."""
    if getattr(ctx, "dry_run", False):
        return None
    try:
        return getattr(ctx, "llm", None)
    except Exception as e:  # noqa: BLE001 - a broken LLM config must never kill reply handling
        log = getattr(ctx, "log", None)
        if log:
            log.warning("LLM unavailable for reply classification: %s", e)
        return None


def _ai_user_prompt(reply: Reply, text: str, ctx: Any) -> str:
    offer = ctx.playbook.offer
    today = _today(ctx)
    sender = offer.get("sender_name") or ""
    if sender and offer.get("sender_title"):
        sender += f", {offer['sender_title']}"
    lines = [
        f"Today's date: {today.isoformat()} ({today.strftime('%A')})",
        "",
        "About us (the sender of the original email):",
        f"- Company: {offer.get('sender_company') or '(not set)'}",
        f"- Sender: {sender or '(not set)'}",
        f"- What we offer: {offer.get('service') or '(not set)'}",
    ]
    if offer.get("value_prop"):
        lines.append(f"- Outcome we deliver: {offer['value_prop']}")
    lines.append(f"- Booking link: {offer.get('booking_link') or '(none)'}")
    if offer.get("language"):
        lines.append(f"- Our language: {offer['language']}")
    lines += ["", "The reply (quoted history removed):", f"From: {reply.from_email or '(unknown)'}",
              f"Subject: {reply.subject or '(none)'}", '"""',
              (text or "(empty)")[:MAX_AI_REPLY_CHARS], '"""']
    return "\n".join(lines)


def _ai_category(value: Any) -> str:
    c = str(value or "").strip().lower()
    c = _CATEGORY_ALIASES.get(c, c)
    if c not in RC.ALL:
        raise ValueError(f"unknown category {value!r}")
    return c


def _ai_date(value: Any) -> Optional[str]:
    if value is None or (isinstance(value, str) and value.strip().lower() in _NULLISH):
        return None
    s = str(value).strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        raise ValueError(f"follow_up_date must be YYYY-MM-DD, got {value!r}")
    date.fromisoformat(s)  # raises ValueError for 2026-02-30
    return s


def _ai_confidence(value: Any) -> float:
    try:
        c = float(value)
    except (TypeError, ValueError):
        return 0.7
    if c > 1.0:
        c = c / 100.0
    return max(0.0, min(1.0, c))


def _ai_str(value: Any, limit: int) -> str:
    s = "" if value is None else str(value).strip()
    return "" if s.lower() in _NULLISH else s[:limit]


def classify_ai(reply: Reply, ctx: Any) -> Reply:
    """Classify with the playbook's LLM (``ctx.llm.complete_json``).

    Expects ``{category, confidence, summary, referral_name, referral_email,
    follow_up_date (YYYY-MM-DD|null), suggested_reply}``. The category must be
    one of ``ReplyCategory.ALL`` (a few obvious aliases are accepted) and the
    date must be a real YYYY-MM-DD date; anything else - or any LLM error, or
    no LLM / dry run - falls back to ``classify_rules``. Missing timing/ooo
    dates and referral addresses are filled in by the rule extractors.
    """
    llm = _llm(ctx)
    if llm is None:
        return classify_rules(reply, ctx)
    text = clean_reply_text(reply.body)
    today = _today(ctx)
    try:
        out = llm.complete_json(AI_SYSTEM_PROMPT, _ai_user_prompt(reply, text, ctx), max_tokens=700)
        if not isinstance(out, dict):
            raise ValueError(f"expected a JSON object, got {type(out).__name__}")
        category = _ai_category(out.get("category"))
        follow_up = _ai_date(out.get("follow_up_date"))
        ref_email = _ai_str(out.get("referral_email"), 254).lower()
        if ref_email and not is_valid_email(ref_email):
            ref_email = ""
        ref_name = _ai_str(out.get("referral_name"), 100)
    except Exception as e:  # noqa: BLE001 - LLMError, HttpError, bad JSON, bad fields ...
        ctx.log.warning("AI reply classifier failed (%s); falling back to rules", e)
        return classify_rules(reply, ctx)

    _reset(reply)
    reply.classifier = "ai"
    reply.category = category
    reply.confidence = _ai_confidence(out.get("confidence"))
    reply.summary = _ai_str(out.get("summary"), 300)
    reply.suggested_reply = _ai_str(out.get("suggested_reply"), 2000)
    if category == RC.REFERRAL:
        if not ref_email:
            ref = extract_referral(text, reply, ctx)
            if ref:
                ref_name = ref_name or ref[0]
                ref_email = ref[1]
        reply.referral_name, reply.referral_email = ref_name, ref_email
    if category == RC.TIMING:
        if not follow_up:
            default_days = _cfg_days(ctx, "timing_default_days", DEFAULT_TIMING_DAYS)
            follow_up = timing_follow_up(text, today, default_days)[0].isoformat()
        reply.follow_up_date = follow_up
    elif category == RC.OOO:
        if not follow_up:
            ret = ooo_return_date(text, today) or ooo_return_date(reply.subject, today)
            follow_up = ret.isoformat() if ret else None
        reply.follow_up_date = follow_up
    if not reply.summary:
        reply.summary = _summ(category.capitalize(), text)
    return reply


# =============================================================================
# classify (dispatcher)
# =============================================================================

def classify(reply: Reply, ctx: Any) -> Reply:
    """Classify per ``replies.classifier`` (rules | ai | auto). Mutates + returns ``reply``."""
    mode = str(ctx.playbook.replies.get("classifier") or "auto").strip().lower()
    if mode == "rules":
        return classify_rules(reply, ctx)
    if mode not in ("ai", "auto"):
        ctx.log.warning("unknown replies.classifier %r; using rules", mode)
        return classify_rules(reply, ctx)
    if mode == "auto":
        probe = classify_rules(replace(reply), ctx)
        machine = (probe.category in AUTO_RULE_CATEGORIES
                   and probe.confidence >= AUTO_RULES_MIN_CONFIDENCE) or _is_daemon(reply.from_email)
        if machine or _llm(ctx) is None:
            for f in fields(Reply):
                setattr(reply, f.name, getattr(probe, f.name))
            return reply
        return classify_ai(reply, ctx)
    if _llm(ctx) is None:
        ctx.log.info("replies.classifier is 'ai' but no LLM is available%s; using rules",
                     " (dry run)" if ctx.dry_run else "")
        return classify_rules(reply, ctx)
    return classify_ai(reply, ctx)


# =============================================================================
# suggest_reply
# =============================================================================

def _first_name(lead: Optional[Lead], email: str) -> str:
    if lead and lead.contact and lead.contact.first_name:
        return lead.contact.first_name.strip().split()[0].capitalize()
    guess = name_from_email(email)
    return guess.split()[0] if guess else ""


def _signoff(ctx: Any) -> str:
    offer = ctx.playbook.offer
    sig = str(offer.get("signature") or "").strip()
    if sig:
        return sig
    name = str(offer.get("sender_name") or "").strip()
    return f"Best,\n{name}" if name else "Best,"


def suggest_reply(reply: Reply, lead: Optional[Lead], ctx: Any) -> str:
    """A short draft answer (templated), or the AI's ``suggested_reply`` when present.

    positive -> thanks + booking link (or "what time works this week?");
    question -> acknowledge + ``[short answer]`` placeholder + booking link;
    referral -> a note *to the referred person* saying who referred us;
    timing   -> "I'll reach out around <date>"; other categories -> "".
    """
    if reply.suggested_reply and reply.suggested_reply.strip():
        return reply.suggested_reply.strip()
    offer = ctx.playbook.offer
    booking = str(offer.get("booking_link") or "").strip()
    today = _today(ctx)
    hi = f"Hi {_first_name(lead, reply.from_email) or 'there'},"
    sign = _signoff(ctx)
    cat = reply.category
    if cat == RC.POSITIVE:
        ask = (f"Here's my calendar so you can grab a time that suits you: {booking}" if booking
               else "What time works for a quick call this week?")
        return f"{hi}\n\nThanks for getting back to me - glad this is relevant.\n\n{ask}\n\n{sign}"
    if cat == RC.QUESTION:
        ask = (f"Happy to walk you through it properly on a quick call - you can grab a time here: {booking}"
               if booking else "Happy to walk you through it properly on a quick call - "
                               "what time works for you this week?")
        return f"{hi}\n\nGood question - [short answer to their question].\n\n{ask}\n\n{sign}"
    if cat == RC.REFERRAL:
        to_first = (reply.referral_name.split()[0] if reply.referral_name
                    else (name_from_email(reply.referral_email).split() or [""])[0])
        referrer = ""
        if lead and lead.contact and lead.contact.full_name and lead.contact.email == reply.from_email:
            referrer = lead.contact.full_name
        referrer = referrer or name_from_email(reply.from_email) or reply.from_email or "a colleague of yours"
        service = str(offer.get("service") or "").strip()
        about = f" about {service}" if service else ""
        lines = [f"Hi {to_first or 'there'},", "", f"{referrer} suggested I get in touch with you{about}."]
        # Same rule as the template writer: value_prop completes "We help teams like X ..."
        # unless it is already a full sentence.
        from .writer.template import _value_line
        vp = _value_line({"value_prop": str(offer.get("value_prop") or ""), "service": "",
                          "company": lead.company.name if lead else ""})
        if vp:
            lines += ["", vp]
        cta = str(offer.get("cta") or "Worth a quick chat?").strip()
        lines += ["", f"{cta} {('You can grab a time here: ' + booking) if booking else ''}".strip(), "", sign]
        return "\n".join(lines)
    if cat == RC.TIMING:
        when = _parse_iso(reply.follow_up_date)
        if when is None:
            when = today + timedelta(days=_cfg_days(ctx, "timing_default_days", DEFAULT_TIMING_DAYS))
        return (f"{hi}\n\nThanks for letting me know - completely understand. "
                f"I'll reach out around {_fmt_date(when, today)}.\n\n{sign}")
    return ""


def _parse_iso(value: Any) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


# =============================================================================
# handle_reply
# =============================================================================

_LABELS = {RC.POSITIVE: "Positive reply", RC.QUESTION: "Question", RC.REFERRAL: "Referral",
           RC.TIMING: "Not now", RC.OOO: "Out of office", RC.NEGATIVE: "Not interested",
           RC.UNSUBSCRIBE: "Unsubscribe", RC.BOUNCE: "Bounce", RC.OTHER: "Reply"}


def _stored_body(raw: str, cleaned: str) -> str:
    return cleaned or truncate(_plain(raw), MAX_STORED_RAW_CHARS)


def _find_duplicate(reply: Reply, body: str, ctx: Any) -> Optional[Reply]:
    """A reply already saved for this playbook with the same sender, time and body."""
    if not reply.received_at or not reply.from_email:
        return None
    conn = getattr(ctx.store, "conn", None)
    if conn is None:
        return None
    try:
        rows = conn.execute(
            "SELECT payload FROM replies WHERE playbook=? AND from_email=? AND received_at=?",
            (ctx.playbook.name, reply.from_email, reply.received_at)).fetchall()
    except Exception:  # noqa: BLE001 - dedupe is best effort
        return None
    names = {f.name for f in fields(Reply)}
    for row in rows:
        try:
            d = json.loads(row[0])
        except (TypeError, ValueError):
            continue
        if isinstance(d, dict) and (d.get("body") or "") == body:
            return Reply(**{k: v for k, v in d.items() if k in names})
    return None


def _link_lead(reply: Reply, email: str, ctx: Any) -> Optional[Lead]:
    store, pb = ctx.store, ctx.playbook
    if reply.lead_id:
        lead = store.get_lead(reply.lead_id)
        if lead:
            return lead
    if not email:
        return None
    lead = store.find_lead_by_email(email, pb.name)
    if lead:
        return lead
    if _is_machine_address(email) or is_personal_email(email):
        return None  # a gmail.com "domain match" would link strangers
    domain = normalize_domain(email)
    leads = store.find_leads_by_domain(domain, pb.name) if domain else []
    return leads[0] if leads else None


def _bounce_target(reply: Reply, raw: str, ctx: Any) -> str:
    cands = extract_bounced_emails(_plain(raw) + "\n" + (reply.subject or ""),
                                   exclude=[reply.from_email], own_domain=_own_domain(ctx))
    for c in cands:
        if ctx.store.find_lead_by_email(c, ctx.playbook.name):
            return c
    if cands:
        return cands[0]
    return "" if _is_machine_address(reply.from_email) else reply.from_email


def _move(lead: Optional[Lead], ctx: Any, actions: List[str], note: str, *stages: str) -> None:
    if not lead:
        return
    changed = False
    for s in stages:
        changed = ctx.store.set_stage(lead.id, s, note=note) or changed
    current = ctx.store.get_lead(lead.id)
    stage = current.stage if current else stages[-1]
    actions.append(f"lead stage -> {stage}" if changed else f"lead stage stays {stage}")


def _suppress(email: str, reason: str, ctx: Any, actions: List[str]) -> None:
    if email and is_valid_email(email):
        ctx.store.suppress(email, "email", reason)
        actions.append(f"suppressed {email} ({reason})")


def _referral_lead(reply: Reply, lead: Optional[Lead], ctx: Any, actions: List[str]) -> Optional[Lead]:
    email = reply.referral_email
    store, pb = ctx.store, ctx.playbook
    if not email:
        who = reply.referral_name or "someone"
        actions.append(f"referred to {who} (no address given - add them manually)")
        return None
    if store.is_suppressed(email=email):
        actions.append(f"referral {email} is suppressed - not added")
        return None
    if store.find_lead_by_email(email, pb.name):
        actions.append(f"referral {email} is already a lead")
        return None
    if lead:
        company = Company.from_dict(copy.deepcopy(lead.company.to_dict()))
    else:
        dom = ""
        for e in (email, reply.from_email):
            if e and not is_personal_email(e):
                dom = normalize_domain(e)
                break
        company = Company(name=dom or "Unknown company", domain=dom)
    contact = Contact(email=email, full_name=reply.referral_name, source="referral")
    new = Lead(company=company, contact=contact, playbook=pb.name, stage=Stage.QUALIFIED,
               notes=[f"referred by {reply.from_email or 'unknown sender'}"])
    if lead:
        new.score, new.tier = lead.score, lead.tier
        new.breakdown = copy.deepcopy(lead.breakdown)
        new.hypothesis = lead.hypothesis
    store.save_lead(new)
    actions.append(f"created lead {new.id} for referral {email}")
    return new


def _alert_content(reply: Reply, lead: Optional[Lead], target: str, ctx: Any
                   ) -> Tuple[str, str, Dict[str, Any]]:
    offer, pb = ctx.playbook.offer, ctx.playbook
    booking = str(offer.get("booking_link") or "").strip()
    contact = lead.contact if lead else None
    who = (contact.full_name if contact and contact.full_name else "") or target or reply.from_email or "unknown sender"
    company = lead.company.name if lead else ""
    title = f"{_LABELS.get(reply.category, 'Reply')}: {who}" + (f" ({company})" if company else "")
    lines: List[str] = []
    if contact and contact.email:
        c = f"Contact: {contact.full_name or contact.email} <{contact.email}>"
        lines.append(c + (f" - {contact.title}" if contact.title else ""))
    if reply.from_email and (not contact or contact.email != reply.from_email):
        lines.append(f"From: {reply.from_email}")
    if lead:
        dom = f" ({lead.company.domain})" if lead.company.domain else ""
        lines.append(f"Company: {lead.company.name}{dom}")
        lines.append(f"Lead score: {lead.score} ({lead.tier})")
    else:
        lines.append("Lead: no matching lead in the database")
    if reply.subject:
        lines.append(f"Subject: {reply.subject}")
    if reply.summary:
        lines.append(f"Summary: {reply.summary}")
    if reply.body:
        lines.append(f"Reply: \"{truncate(reply.body, 500)}\"")
    if reply.category == RC.REFERRAL and (reply.referral_name or reply.referral_email):
        ref = " ".join(x for x in (reply.referral_name, f"<{reply.referral_email}>" if reply.referral_email else "") if x)
        lines.append(f"Referred to: {ref}")
    if reply.follow_up_date:
        lines.append(f"Follow-up: {reply.follow_up_date}")
    if booking and reply.category in (RC.POSITIVE, RC.QUESTION, RC.REFERRAL):
        lines.append(f"Booking link: {booking}")
    if reply.suggested_reply:
        lines += ["", "Suggested reply:", reply.suggested_reply]
    data = {
        "playbook": pb.name,
        "category": reply.category,
        "lead_id": lead.id if lead else "",
        "company": company,
        "domain": lead.company.domain if lead else normalize_domain(target) if target else "",
        "contact": contact.to_dict() if contact else None,
        "booking_link": booking,
        "reply": reply.to_dict(),
    }
    return title, "\n".join(lines), data


def handle_reply(reply: Reply, ctx: Any) -> Reply:
    """Classify a reply, link it to its lead, act on it, alert and save it.

    See the module docstring for the action taken per category. Returns the
    (mutated) reply with ``lead_id``, classification, ``suggested_reply`` and a
    readable ``action`` string filled in. Requires ``ctx.store``.
    """
    store, pb = ctx.store, ctx.playbook
    if store is None:
        raise RuntimeError("handle_reply needs ctx.store (a leadgen.store.Store)")
    today = _today(ctx)
    raw = reply.body or ""
    body = _stored_body(raw, clean_reply_text(raw))
    dup = _find_duplicate(reply, body, ctx)
    if dup is not None:
        dup.action = "duplicate - already handled, skipped"
        ctx.log.info("reply from %s at %s already handled; skipping", reply.from_email, reply.received_at)
        return dup

    classify(reply, ctx)
    reply.body = body
    cat = reply.category
    target = _bounce_target(reply, raw, ctx) if cat == RC.BOUNCE else reply.from_email
    lead = _link_lead(reply, target, ctx)
    if lead:
        reply.lead_id = lead.id

    actions: List[str] = [] if lead else ["no matching lead"]
    note = f"reply: {cat}"
    if cat == RC.TIMING:
        days = _cfg_days(ctx, "timing_default_days", DEFAULT_TIMING_DAYS)
        due = _parse_iso(reply.follow_up_date) or today + timedelta(days=days)
        due = max(due, today + timedelta(days=1))
        reply.follow_up_date = due.isoformat()

    reply.suggested_reply = suggest_reply(reply, lead, ctx)

    if cat == RC.POSITIVE:
        _move(lead, ctx, actions, note, Stage.REPLIED, Stage.POSITIVE)
    elif cat in (RC.QUESTION, RC.REFERRAL):
        _move(lead, ctx, actions, note, Stage.REPLIED)
        if cat == RC.REFERRAL:
            _referral_lead(reply, lead, ctx, actions)
    elif cat == RC.TIMING:
        _move(lead, ctx, actions, note, Stage.REPLIED)
        due = _parse_iso(reply.follow_up_date) or today + timedelta(days=DEFAULT_TIMING_DAYS)
        store.schedule_followup(due, "timing", lead_id=lead.id if lead else "",
                                email=reply.from_email, playbook=pb.name)
        actions.append(f"follow-up scheduled {due.isoformat()} (timing)")
    elif cat == RC.OOO:
        ret = _parse_iso(reply.follow_up_date)
        if ret:
            due = ret + timedelta(days=1)
        else:
            due = today + timedelta(days=_cfg_days(ctx, "ooo_default_days", DEFAULT_OOO_DAYS))
        due = max(due, today + timedelta(days=1))
        store.schedule_followup(due, "ooo", lead_id=lead.id if lead else "",
                                email=reply.from_email, playbook=pb.name)
        actions.append(f"follow-up scheduled {due.isoformat()} (out of office"
                       f"{'' if ret else ', no return date'})")
    elif cat == RC.NEGATIVE:
        _move(lead, ctx, actions, note, Stage.REPLIED, Stage.LOST)
        _suppress(reply.from_email, "not interested", ctx, actions)
    elif cat == RC.UNSUBSCRIBE:
        _move(lead, ctx, actions, note, Stage.LOST)
        _suppress(reply.from_email, "unsubscribed", ctx, actions)
    elif cat == RC.BOUNCE:
        if target:
            _move(lead, ctx, actions, note, Stage.LOST)
            _suppress(target, "bounced", ctx, actions)
            store.put_verification(target, "invalid", "bounce")
            actions.append(f"marked {target} invalid")
        else:
            actions.append("bounced address not found in the notice - nothing suppressed")
    else:  # other
        if _is_machine_address(reply.from_email):
            actions.append("automated notice - no action")
        else:
            _move(lead, ctx, actions, note, Stage.REPLIED)

    title, text, data = _alert_content(reply, lead, target, ctx)
    sent = notify(ctx, cat, title, text, data)
    if sent:
        actions.append(f"alerted {sent} channel(s)")
    reply.action = "; ".join(actions) if actions else "recorded"
    store.save_reply(reply, pb.name)
    return reply


# =============================================================================
# Inputs: CSV + webhooks
# =============================================================================

_CSV_FROM = ("from", "from_email", "email", "sender", "sender_email", "from_address", "reply_from",
             "lead_email")
_CSV_SUBJECT = ("subject", "reply_subject")
_CSV_BODY = ("body", "text", "message", "reply", "content", "reply_text", "reply_body", "html")
_CSV_DATE = ("received_at", "date", "timestamp", "received", "time", "sent_at")
_CSV_LEAD = ("lead_id",)


def _addr(value: Any) -> str:
    """'Jane Doe <Jane@Acme.com>' / 'jane@acme.com' -> 'jane@acme.com' ('' if none)."""
    if not value or not isinstance(value, (str, bytes)):
        return ""
    s = value.decode("utf-8", "replace") if isinstance(value, bytes) else value
    _, email = parseaddr(s.strip())
    email = (email or "").strip().lower()
    if is_valid_email(email):
        return email
    m = _EMAIL_RE.search(s)
    return m.group(0).lower() if m and is_valid_email(m.group(0).lower()) else ""


def _norm_header(h: str) -> str:
    return re.sub(r"[\s\-]+", "_", (h or "").strip().lower())


def load_replies_csv(path: Any) -> List[Reply]:
    """Read replies from a CSV (or ``.tsv``) export.

    Headers are matched case-insensitively (spaces/dashes = underscores):
    sender ``from`` / ``from_email`` / ``email`` / ``sender`` (``"Jane
    <jane@x.com>"`` is fine), ``subject``, body ``body`` / ``text`` /
    ``message`` / ``reply`` / ``content``, ``received_at`` / ``date`` /
    ``timestamp`` and ``lead_id``. Rows without a sender (or lead_id) or
    without any text are skipped.
    """
    p = Path(path)
    with p.open(newline="", encoding="utf-8-sig") as f:
        head = f.readline()
        f.seek(0)
        delim = "\t" if p.suffix.lower() == ".tsv" or ("\t" in head and "," not in head) else ","
        reader = csv.DictReader(f, delimiter=delim)
        cols = {_norm_header(h): h for h in (reader.fieldnames or []) if h}
        out: List[Reply] = []
        for row in reader:
            def get(keys: Sequence[str]) -> str:
                for k in keys:
                    col = cols.get(k)
                    v = (row.get(col) or "").strip() if col else ""
                    if v:
                        return v
                return ""

            email = ""
            for k in _CSV_FROM:
                col = cols.get(k)
                email = _addr(row.get(col)) if col else ""
                if email:
                    break
            body, subject, lead_id = get(_CSV_BODY), get(_CSV_SUBJECT), get(_CSV_LEAD)
            if not (email or lead_id) or not (body or subject):
                continue
            out.append(Reply(from_email=email, body=body, subject=subject,
                             received_at=get(_CSV_DATE), lead_id=lead_id))
    return out


_INSTANTLY_EVENTS = {"reply_received", "auto_reply_received", "email_replied"}
_SMARTLEAD_EVENTS = {"email_reply", "email_replied"}
_GENERIC_REPLY_EVENTS = {"reply", "replied", "reply_received", "email_reply", "email_replied",
                         "inbound", "inbound_email", "message_received", "email_received"}
_BOUNCE_EVENTS = {"email_bounced", "email_bounce", "bounce", "bounced", "lead_bounced",
                  "hard_bounce"}
_UNSUB_EVENTS = {"lead_unsubscribed", "unsubscribed", "unsubscribe", "email_unsubscribed",
                 "lead_unsubscribe"}
_REPLY_KEYS = ("reply_text", "reply_html", "reply_snippet", "reply_text_snippet", "reply_message",
               "reply_body", "body", "text", "message", "content")


def _ts(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()
                                           and len(value.strip()) >= 9):
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).replace(microsecond=0).isoformat()
        except (OverflowError, OSError, ValueError):
            return str(value)
    return str(value).strip()


def _text_of(value: Any) -> str:
    """Body text from a str or a ``{text, html, body, content}`` dict."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for k in ("text", "plain", "body", "content", "html"):
            v = value.get(k)
            if isinstance(v, str) and v.strip():
                return v
    return ""


def _first_text(payload: Dict[str, Any], keys: Sequence[str]) -> str:
    for k in keys:
        v = _text_of(get_path(payload, k))
        if v.strip():
            return v
    return ""


def _first_addr(payload: Dict[str, Any], keys: Sequence[str]) -> str:
    for k in keys:
        e = _addr(get_path(payload, k))
        if e:
            return e
    return ""


def _unwrap(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Some tools wrap the event: {"data": {...}} / {"payload": {...}} / {"event": {...}}."""
    for _ in range(3):
        if any(k in payload for k in ("lead_email", "sl_lead_email", "from_email", "email", "from")):
            return payload
        inner = next((payload[k] for k in ("data", "payload", "body", "event") if isinstance(payload.get(k), dict)),
                     None)
        if inner is None:
            return payload
        merged = dict(inner)
        for k in ("event_type", "event", "type", "timestamp"):
            if k in payload and not isinstance(payload[k], dict):
                merged.setdefault(k, payload[k])
        payload = merged
    return payload


def parse_webhook_payload(payload: Any) -> Optional[Reply]:
    """Turn a reply webhook body into a ``Reply`` (None for non-reply events / junk).

    Accepted shapes (liberal - extra fields are ignored, one wrapping level of
    ``data`` / ``payload`` / ``event`` is unwrapped):

    * **Instantly** - ``event_type`` ``reply_received`` (or
      ``auto_reply_received``): ``lead_email``, ``reply_text`` /
      ``reply_html`` / ``reply_text_snippet`` / ``reply_snippet``,
      ``reply_subject``, ``timestamp``, ``campaign_id``.
    * **Smartlead** - ``event_type`` ``EMAIL_REPLY``: ``sl_lead_email`` /
      ``lead_email`` / ``to_email`` (then ``leadCorrespondence.replyReceivedFrom``),
      ``reply_message: {text, html, time}`` or ``reply_body`` /
      ``preview_text``, ``subject``, ``time_replied`` / ``event_timestamp``.
    * **Generic** - ``{from_email | email | from | sender, body | text |
      message | reply | content | html, subject, received_at | date |
      timestamp, lead_id}``.

    Delivery events are mapped too, so they flow through the same actions:
    ``email_bounced`` / ``EMAIL_BOUNCE`` -> a bounce notice for the lead's
    address, ``lead_unsubscribed`` / ``LEAD_UNSUBSCRIBED`` -> an unsubscribe.
    Every other event (sent, opened, clicked, ...) returns None.
    """
    if not isinstance(payload, dict):
        return None
    p = _unwrap(payload)
    ev_raw = p.get("event_type") or p.get("event") or p.get("type") or ""
    event = str(ev_raw).strip().lower() if isinstance(ev_raw, str) else ""
    received = _ts(p.get("time_replied") or p.get("event_timestamp") or get_path(p, "reply_message.time")
                   or p.get("timestamp") or p.get("received_at") or p.get("date") or p.get("time"))
    lead_addr_keys = ("sl_lead_email", "lead_email", "lead.email", "email", "to_email",
                      "leadCorrespondence.targetLeadEmail")

    if event in _BOUNCE_EVENTS:
        email = _first_addr(p, lead_addr_keys)
        if not email:
            return None
        return Reply(from_email=email, subject="Undeliverable (bounce event)", received_at=received,
                     body=f"Delivery failure: the email to {email} bounced (provider event "
                          f"'{event}'). Address not found.")
    if event in _UNSUB_EVENTS:
        email = _first_addr(p, lead_addr_keys)
        if not email:
            return None
        return Reply(from_email=email, subject="Unsubscribe", received_at=received,
                     body=f"Please unsubscribe me (provider event '{event}').")

    if event and event not in _INSTANTLY_EVENTS | _SMARTLEAD_EVENTS | _GENERIC_REPLY_EVENTS:
        return None  # email_sent, email_opened, link_clicked, lead_interested, ...
    smartlead = event in _SMARTLEAD_EVENTS or "sl_lead_email" in p or isinstance(p.get("reply_message"), dict)
    instantly = not smartlead and (event in _INSTANTLY_EVENTS or any(
        k in p for k in ("reply_text", "reply_html", "reply_snippet", "reply_text_snippet")))

    if smartlead:
        email = _first_addr(p, ("sl_lead_email", "lead_email", "to_email",
                                "leadCorrespondence.replyReceivedFrom", "lead.email"))
        body = _first_text(p, ("reply_message", "reply_body", "preview_text", "body", "text"))
        subject = str(p.get("subject") or get_path(p, "reply_message.subject") or "")
        lead_id = ""
    elif instantly:
        email = _first_addr(p, ("lead_email", "email", "from_email", "lead.email"))
        body = _first_text(p, ("reply_text", "reply_html", "reply_text_snippet", "reply_snippet",
                               "body", "text"))
        subject = str(p.get("reply_subject") or p.get("subject") or "")
        lead_id = ""
    else:
        email = _first_addr(p, ("from_email", "email", "from", "sender", "sender_email", "lead_email"))
        body = _first_text(p, ("body", "text", "message", "reply", "content", "html", "snippet"))
        subject = str(p.get("subject") or "")
        lid = p.get("lead_id")
        lead_id = str(lid) if isinstance(lid, (str, int)) and not isinstance(lid, bool) else ""
    if not email or not (body.strip() or subject.strip()):
        return None
    return Reply(from_email=email, body=body, subject=subject.strip(), received_at=received,
                 lead_id=lead_id)


__all__ = [
    "AI_SYSTEM_PROMPT", "classify", "classify_ai", "classify_rules", "clean_reply_text",
    "extract_bounced_emails", "extract_referral", "handle_reply", "load_replies_csv",
    "name_from_email", "ooo_return_date", "parse_webhook_payload", "suggest_reply",
    "timing_follow_up",
]
