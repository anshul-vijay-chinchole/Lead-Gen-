"""File exporters (``type: csv`` / ``type: json``) + the row helpers every exporter shares.

Both exporters are offline (they only write files into the run's ``out_dir``)
and have ``scope = "all"``: they receive every scored lead, not just the
send-eligible ones, because they are review artefacts.

``type: csv`` -> ``opportunities.csv``
--------------------------------------
A Google-Sheets-ready review sheet, one row per lead, sorted by score
(highest first). Columns (see ``lead_rows``)::

    lead_id, score, tier, stage, company, domain, industry, employees, location,
    top_signal_type, top_signal, signal_age_days, signal_url, signal_count,
    contact_name, contact_title, email, email_status, linkedin, phone,
    personalization, hypothesis, subject_1, email_1, followup_1 .. followup_N,
    score_reasons, notes, sources

``N`` is the largest number of follow-ups any lead has. ``signal_age_days`` is
measured against ``ctx.today``. ``score_reasons`` and ``notes`` are joined
with ``"; "``.

Message convention (shared with the writers): step 1 has a subject; a
follow-up with subject ``""`` is a *reply in the same thread*. A follow-up
that carries its own subject starts a new thread; in the review sheet its
cell reads ``Subject: <subject>`` + blank line + body.

CSV / formula injection: company names, job titles etc. come from scraped
third-party data, so any text cell starting with ``= + - @`` (or a tab / CR)
is prefixed with a single quote before it is written, which makes Excel /
Google Sheets treat it as text instead of a formula.

``type: json`` -> ``leads.json``
--------------------------------
``[lead.to_dict(), ...]`` (sorted by score, highest first), pretty-printed,
UTF-8.

Config keys (both exporters)
----------------------------
filename       Output file name, relative to ``out_dir`` (default
               ``opportunities.csv`` / ``leads.json``; an absolute path is used
               as-is). Parent directories are created.
encoding       File encoding (default ``utf-8``; use ``utf-8-sig`` if the
               sheet is opened directly in Excel and accents look wrong).
formula_guard  csv only: prefix formula-looking cells with ``'`` (default true).
label          Optional name reported in ``ExportResult.exporter`` (default:
               the exporter type).

Shared helpers used by the other exporters
------------------------------------------
``lead_rows(leads, today)``        -> ``(header, rows)`` for review sheets (CSV, Google Sheets).
``outbound_row(lead, steps, ...)`` -> flat dict of sending fields (name, company,
                                     website, sequence copy, signal, score, ...).
``sequence_keys`` / ``sequence_fields`` -> ``subject_1, email_1, email_2 .. email_N``.
``write_csv`` / ``guard_cell``     -> injection-safe CSV writing.
``UploadCsvExporter``              -> base class for sending-tool upload CSVs
                                     (Instantly, Smartlead).
``response_error`` / ``FATAL_STATUSES`` / ``config_int`` -> shared by the API senders.
"""
from __future__ import annotations

import csv
import html
import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..models import Company, Lead, Message
from ..utils import normalize_text
from .base import Exporter, ExportResult

#: Fixed review-sheet columns before / after the sequence columns.
REVIEW_COLUMNS_HEAD: Tuple[str, ...] = (
    "lead_id", "score", "tier", "stage", "company", "domain", "industry", "employees",
    "location", "top_signal_type", "top_signal", "signal_age_days", "signal_url",
    "signal_count", "contact_name", "contact_title", "email", "email_status", "linkedin",
    "phone", "personalization", "hypothesis", "subject_1", "email_1",
)
REVIEW_COLUMNS_TAIL: Tuple[str, ...] = ("score_reasons", "notes", "sources")

#: First characters that make spreadsheet apps evaluate a cell as a formula.
FORMULA_PREFIXES: Tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")

# A value made only of digits / spaces / ( ) . - / (optionally starting with +)
# cannot carry a formula payload; used to leave phone numbers untouched in
# upload CSVs, where a leading quote would end up in the sending tool.
_PHONE_RE = re.compile(r"^\+?[\d(][\d\s().\-/]{3,}$")

# Trailing legal suffixes dropped from company names used in email copy
# ("Acme Inc." -> "Acme"). Group / Holdings are kept: they are often the brand.
_LEGAL_SUFFIX_RE = re.compile(
    r"[\s,]+(?:inc|incorporated|llc|l\.l\.c|ltd|limited|plc|corp|corporation|gmbh|ag|s\.?a\.?|"
    r"sas|b\.?v\.?|n\.?v\.?|pty|pte|llp|lp|co)\.?\s*$",
    re.I,
)

BODY_FORMATS = ("text", "html")


# --- small helpers --------------------------------------------------------------------

def guard_cell(value: Any) -> Any:
    """Neutralise CSV/formula injection: prefix ``'`` to text starting with = + - @ tab CR.

    Non-strings (ints, floats, None) are returned unchanged.
    """
    if isinstance(value, str) and value and value[0] in FORMULA_PREFIXES:
        return "'" + value
    return value


def _cell(value: Any) -> Any:
    """None -> '' so csv/Sheets never see ``None``."""
    return "" if value is None else value


def sorted_by_score(leads: Iterable[Lead]) -> List[Lead]:
    """Stable sort, highest score first (ties keep their input order)."""
    return sorted(leads, key=lambda ld: -(ld.score or 0))


def ordered_messages(lead: Lead) -> List[Message]:
    """The lead's messages in sequence order (by ``step``, then original order)."""
    msgs = list(lead.messages or [])
    return [m for _, m in sorted(enumerate(msgs), key=lambda t: (_step(t[1], t[0]), t[0]))]


def _step(msg: Any, fallback: int) -> int:
    try:
        return int(getattr(msg, "step", fallback + 1))
    except (TypeError, ValueError):
        return fallback + 1


def max_steps(leads: Iterable[Lead]) -> int:
    """Largest number of sequence messages among ``leads`` (0 when none are written)."""
    return max((len(ld.messages or []) for ld in leads), default=0)


def followup_subject_steps(leads: Iterable[Lead]) -> List[int]:
    """1-based step numbers (>= 2) where at least one lead has a non-empty follow-up subject.

    By convention follow-ups have subject ``""`` (reply in thread); a non-empty
    one means "new thread", so exporters add a ``subject_<k>`` column for it.
    """
    steps = set()
    for ld in leads:
        for i, m in enumerate(ordered_messages(ld)):
            if i >= 1 and (m.subject or "").strip():
                steps.add(i + 1)
    return sorted(steps)


def format_body(text: str, body_format: str = "text") -> str:
    """Return an email body as plain text (default) or minimal HTML (``<br>`` line breaks).

    Use ``html`` when a sending tool collapses line breaks inside variables.
    """
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if body_format == "html":
        return html.escape(text, quote=False).replace("\n", "<br>")
    return text


def website_url(company: Company) -> str:
    """Company website as an absolute URL (``https://acme.com``), or ''."""
    site = (company.website or "").strip()
    if site:
        return site if "://" in site else "https://" + site.lstrip("/")
    return f"https://{company.domain}" if company.domain else ""


def display_company_name(name: str) -> str:
    """'Acme Holdings Ltd.' -> 'Acme Holdings'; keeps the original if stripping empties it."""
    original = (name or "").strip()
    cleaned = original
    for _ in range(3):  # "Acme Pty Ltd" needs two passes
        new = _LEGAL_SUFFIX_RE.sub("", cleaned).strip().rstrip(",").strip()
        if new == cleaned:
            break
        cleaned = new
    return cleaned or original


def company_location(company: Company) -> str:
    """'London' + 'UK' -> 'London, UK' (country only added when not already mentioned)."""
    loc = (company.location or "").strip()
    country = (company.country or "").strip()
    if country and normalize_text(country) not in normalize_text(loc):
        return f"{loc}, {country}" if loc else country
    return loc


def _join(values: Iterable[Any], sep: str = "; ") -> str:
    return sep.join(str(v).strip() for v in values if v is not None and str(v).strip())


# --- sequence + outbound rows ---------------------------------------------------------

def sequence_keys(steps: int, subject_steps: Sequence[int] = ()) -> List[str]:
    """``subject_1, email_1, [subject_2,] email_2 .. email_<steps>`` (at least step 1)."""
    keys = ["subject_1", "email_1"]
    extra = set(subject_steps)
    for k in range(2, max(1, steps) + 1):
        if k in extra:
            keys.append(f"subject_{k}")
        keys.append(f"email_{k}")
    return keys


def sequence_fields(lead: Lead, steps: int, subject_steps: Sequence[int] = (),
                    body_format: str = "text") -> Dict[str, str]:
    """Sequence copy as flat variables (``sequence_keys`` order); missing steps are ''."""
    msgs = ordered_messages(lead)
    out: Dict[str, str] = {k: "" for k in sequence_keys(steps, subject_steps)}
    for i, m in enumerate(msgs):
        k = i + 1
        if f"email_{k}" not in out:
            continue
        out[f"email_{k}"] = format_body(m.body or "", body_format)
        if k == 1:
            out["subject_1"] = (m.subject or "").strip()
        elif f"subject_{k}" in out:
            out[f"subject_{k}"] = (m.subject or "").strip()
    return out


def outbound_row(lead: Lead, steps: Optional[int] = None, subject_steps: Sequence[int] = (),
                 body_format: str = "text", clean_company: bool = True) -> Dict[str, Any]:
    """Flat dict of everything a sending tool needs for one lead.

    Keys: ``lead_id, email, first_name, last_name, full_name, company_name,
    website, domain, job_title, linkedin_url, phone, location, personalization,
    hypothesis, signal, signal_type, signal_url, score, tier`` + the
    ``sequence_fields`` (``subject_1, email_1, email_2 ..``).

    ``steps`` defaults to this lead's own message count; pass the max over a
    batch so every row has the same keys. ``company_name`` drops legal
    suffixes (``Inc``, ``Ltd`` ...) unless ``clean_company`` is False.
    """
    c = lead.company
    ct = lead.contact
    sig = lead.top_signal
    n = len(lead.messages or []) if steps is None else steps
    row: Dict[str, Any] = {
        "lead_id": lead.id,
        "email": (ct.email if ct else "") or "",
        "first_name": (ct.first_name if ct else "") or "",
        "last_name": (ct.last_name if ct else "") or "",
        "full_name": (ct.full_name if ct else "") or "",
        "company_name": display_company_name(c.name) if clean_company else c.name,
        "website": website_url(c),
        "domain": c.domain or "",
        "job_title": (ct.title if ct else "") or "",
        "linkedin_url": (ct.linkedin_url if ct else "") or "",
        "phone": (ct.phone if ct else "") or "",
        "location": ((ct.location if ct else "") or company_location(c)),
        "personalization": lead.personalization or "",
        "hypothesis": lead.hypothesis or "",
        "signal": (sig.title if sig else "") or "",
        "signal_type": (sig.type if sig else "") or "",
        "signal_url": (sig.url if sig else "") or "",
        "score": int(lead.score or 0),
        "tier": lead.tier or "",
    }
    row.update(sequence_fields(lead, n, subject_steps, body_format))
    return row


# --- review sheet rows ----------------------------------------------------------------

def review_header(followups: int) -> List[str]:
    return (list(REVIEW_COLUMNS_HEAD) + [f"followup_{i}" for i in range(1, followups + 1)]
            + list(REVIEW_COLUMNS_TAIL))


def lead_rows(leads: Iterable[Lead], today: Optional[date] = None) -> Tuple[List[str], List[List[Any]]]:
    """Review-sheet ``(header, rows)`` for ``leads``, sorted by score (highest first).

    Values are native types (ints for score / employees / signal_age_days /
    signal_count, strings otherwise, '' for missing) so Google Sheets keeps
    numbers sortable. No formula guarding here - ``write_csv`` does that.
    """
    today = today or date.today()
    ordered = sorted_by_score(leads)
    followups = max(0, max_steps(ordered) - 1)
    header = review_header(followups)
    rows: List[List[Any]] = []
    for lead in ordered:
        c, ct, sig = lead.company, lead.contact, lead.top_signal
        msgs = ordered_messages(lead)
        first = msgs[0] if msgs else None
        age = sig.age_days(today) if sig else None
        row: List[Any] = [
            lead.id,
            int(lead.score or 0),
            lead.tier or "",
            lead.stage or "",
            c.name or "",
            c.domain or "",
            c.industry or "",
            "" if c.employees is None else c.employees,
            company_location(c),
            (sig.type if sig else "") or "",
            (sig.title if sig else "") or "",
            "" if age is None else age,
            (sig.url if sig else "") or "",
            len(c.signals or []),
            (ct.full_name if ct else "") or "",
            (ct.title if ct else "") or "",
            (ct.email if ct else "") or "",
            (ct.email_status if ct else "") or "",
            (ct.linkedin_url if ct else "") or "",
            (ct.phone if ct else "") or "",
            lead.personalization or "",
            lead.hypothesis or "",
            ((first.subject if first else "") or "").strip(),
            (first.body if first else "") or "",
        ]
        for i in range(1, followups + 1):
            if i < len(msgs):
                m = msgs[i]
                subject = (m.subject or "").strip()
                body = m.body or ""
                row.append(f"Subject: {subject}\n\n{body}" if subject else body)
            else:
                row.append("")
        row += [
            _join(lead.breakdown.reasons if lead.breakdown else []),
            _join(lead.notes or []),
            _join(c.sources or [], ", "),
        ]
        rows.append(row)
    return header, rows


def rows_as_dicts(header: Sequence[str], rows: Iterable[Sequence[Any]]) -> List[Dict[str, Any]]:
    return [dict(zip(header, r)) for r in rows]


# --- file writing ---------------------------------------------------------------------

def resolve_output_path(out_dir: Any, config: Dict[str, Any], default_name: str) -> Path:
    """``out_dir / config['filename']`` (default ``default_name``); creates parent dirs."""
    name = str(config.get("filename") or default_name).strip() or default_name
    path = Path(out_dir) / Path(name).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence[Any]], *,
              guard: bool = True, phone_columns: Sequence[str] = (),
              encoding: str = "utf-8") -> int:
    """Write a CSV with a header row; returns the number of data rows written.

    With ``guard`` every text cell starting with a formula character is
    prefixed with ``'``, except values in ``phone_columns`` that look like a
    plain phone number (``+44 20 7946 0958``), which cannot carry a formula.
    """
    phone_idx = {i for i, h in enumerate(header) if h in set(phone_columns)}
    n = 0
    with open(path, "w", newline="", encoding=encoding) as f:
        w = csv.writer(f)
        w.writerow(list(header))
        for row in rows:
            out = []
            for i, v in enumerate(row):
                v = _cell(v)
                if guard and not (i in phone_idx and isinstance(v, str) and _PHONE_RE.match(v)):
                    v = guard_cell(v)
                out.append(v)
            w.writerow(out)
            n += 1
    return n


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "no", "off", "")
    return bool(value)


def config_int(config: Dict[str, Any], key: str, default: int, minimum: int = 1) -> int:
    """Read an int config value, falling back to ``default`` when missing/invalid."""
    try:
        value = int(config.get(key) if config.get(key) not in (None, "") else default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


# --- sender helpers (Instantly / Smartlead / webhook) ---------------------------------

#: Statuses that mean "every further request will fail too" (bad key, no access,
#: wrong campaign / endpoint): senders stop instead of hammering the API.
FATAL_STATUSES: Tuple[int, ...] = (401, 403, 404)


def response_error(resp: Any) -> str:
    """Best human-readable error text from a provider response (JSON message or raw body)."""
    try:
        data = resp.json()
    except (ValueError, TypeError):
        data = None
    if isinstance(data, dict):
        for key in ("message", "error", "detail", "errors", "msg"):
            val = data.get(key)
            if val:
                if isinstance(val, dict):
                    val = val.get("message") or json.dumps(val)[:200]
                elif isinstance(val, list):
                    val = "; ".join(str(v.get("message", v)) if isinstance(v, dict) else str(v)
                                    for v in val[:5])
                return str(val)[:300]
    text = (getattr(resp, "text", "") or "").strip()
    return text[:300] or "no response body"



class _FileExporter(Exporter):
    """Shared bits for exporters that write one file into ``out_dir``."""

    offline = True
    default_filename = "export.csv"

    @property
    def label(self) -> str:
        return str(self.config.get("label") or self.name)

    @property
    def encoding(self) -> str:
        return str(self.config.get("encoding") or "utf-8")

    def output_path(self, out_dir: Any) -> Path:
        return resolve_output_path(out_dir, self.config, self.default_filename)


# --- exporters ------------------------------------------------------------------------

class CsvExporter(_FileExporter):
    """Google-Sheets-ready review sheet of ALL leads (``opportunities.csv``).

    Config: ``filename``, ``encoding``, ``formula_guard`` (default true),
    ``label`` - see the module docstring.
    """

    name = "csv"
    scope = "all"
    default_filename = "opportunities.csv"

    def export(self, leads: List[Lead], out_dir: Path) -> ExportResult:
        header, rows = lead_rows(leads, self.ctx.today)
        path = self.output_path(out_dir)
        n = write_csv(path, header, rows, guard=_bool(self.config.get("formula_guard"), True),
                      encoding=self.encoding)
        self.log.info("csv: wrote %d leads to %s", n, path)
        return ExportResult(exporter=self.label, count=n, path=str(path), detail=f"{n} leads",
                            exported_ids=[r[0] for r in rows])


class JsonExporter(_FileExporter):
    """Every lead as ``lead.to_dict()`` in a pretty-printed JSON array (``leads.json``).

    Config: ``filename``, ``encoding``, ``label``, ``indent`` (default 2).
    """

    name = "json"
    scope = "all"
    default_filename = "leads.json"

    def export(self, leads: List[Lead], out_dir: Path) -> ExportResult:
        ordered = sorted_by_score(leads)
        try:
            indent = int(self.config.get("indent", 2))
        except (TypeError, ValueError):
            indent = 2
        path = self.output_path(out_dir)
        payload = [ld.to_dict() for ld in ordered]
        path.write_text(json.dumps(payload, indent=indent, ensure_ascii=False, default=str) + "\n",
                        encoding=self.encoding)
        self.log.info("json: wrote %d leads to %s", len(payload), path)
        return ExportResult(exporter=self.label, count=len(payload), path=str(path),
                            detail=f"{len(payload)} leads", exported_ids=[ld.id for ld in ordered])


class UploadCsvExporter(_FileExporter):
    """Base for sending-tool upload CSVs (one row per send-eligible lead with an email).

    Subclasses set ``standard_columns`` (``(csv column, outbound_row key)``
    pairs mapped to the tool's built-in lead fields) and ``custom_keys``
    (``outbound_row`` keys added as custom columns before the sequence
    columns). After those come ``subject_1, email_1, email_2 .. email_N``
    (+ ``subject_<k>`` for follow-ups that start a new thread) and
    ``signal, score, tier``.

    Config keys: ``filename``, ``encoding``, ``label``, ``formula_guard``
    (default true; plain phone numbers are never prefixed), ``body_format``
    (``text`` default | ``html`` = ``<br>`` line breaks), ``clean_company_name``
    (default true: ``Acme Inc.`` -> ``Acme``).
    Leads without an email are skipped (reported in ``detail``).
    """

    scope = "outbound"
    default_filename = "upload.csv"
    standard_columns: Tuple[Tuple[str, str], ...] = ()
    custom_keys: Tuple[str, ...] = ()
    trailing_keys: Tuple[str, ...] = ("signal", "score", "tier")

    def columns(self, steps: int, subject_steps: Sequence[int] = ()) -> List[Tuple[str, str]]:
        cols = list(self.standard_columns)
        cols += [(k, k) for k in self.custom_keys]
        cols += [(k, k) for k in sequence_keys(steps, subject_steps)]
        cols += [(k, k) for k in self.trailing_keys]
        return cols

    @property
    def body_format(self) -> str:
        fmt = str(self.config.get("body_format") or "text").lower()
        if fmt not in BODY_FORMATS:
            raise ValueError(f"{self.name}: body_format must be one of {', '.join(BODY_FORMATS)}, got {fmt!r}")
        return fmt

    def export(self, leads: List[Lead], out_dir: Path) -> ExportResult:
        ordered = sorted_by_score(leads)
        sendable = [ld for ld in ordered if ld.contact and ld.contact.email]
        skipped = len(ordered) - len(sendable)
        # size the sequence columns from the rows actually written
        steps = max(1, max_steps(sendable))
        subject_steps = followup_subject_steps(sendable)
        cols = self.columns(steps, subject_steps)
        fmt = self.body_format
        clean = _bool(self.config.get("clean_company_name"), True)
        rows: List[List[Any]] = []
        ids: List[str] = []
        for lead in sendable:
            row = outbound_row(lead, steps, subject_steps, fmt, clean_company=clean)
            rows.append([row.get(key, "") for _, key in cols])
            ids.append(lead.id)
        path = self.output_path(out_dir)
        header = [c for c, _ in cols]
        phone_cols = [c for c, k in cols if k == "phone"]
        n = write_csv(path, header, rows, guard=_bool(self.config.get("formula_guard"), True),
                      phone_columns=phone_cols, encoding=self.encoding)
        detail = f"{n} leads"
        if skipped:
            detail += f" ({skipped} skipped: no email)"
            self.log.warning("%s: skipped %d lead(s) without an email address", self.name, skipped)
        self.log.info("%s: wrote %d leads to %s", self.name, n, path)
        return ExportResult(exporter=self.label, count=n, path=str(path), detail=detail,
                            exported_ids=ids)


__all__ = [
    "CsvExporter", "JsonExporter", "UploadCsvExporter", "lead_rows", "outbound_row",
    "sequence_keys", "sequence_fields", "write_csv", "guard_cell", "resolve_output_path",
    "max_steps", "followup_subject_steps", "ordered_messages", "sorted_by_score",
    "display_company_name", "website_url", "company_location", "format_body", "review_header",
    "rows_as_dicts", "REVIEW_COLUMNS_HEAD", "REVIEW_COLUMNS_TAIL", "BODY_FORMATS",
    "FATAL_STATUSES", "response_error", "config_int",
]
