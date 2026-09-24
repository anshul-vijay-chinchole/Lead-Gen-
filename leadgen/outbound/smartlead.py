"""Smartlead.ai: upload CSV (``type: smartlead_csv``) and API sender (``type: smartlead``).

Both receive only send-eligible leads (``scope = "outbound"``); the pipeline
marks what they hand over as EXPORTED so nobody is handed over twice.

Setting up the Smartlead campaign
---------------------------------
The engine writes the whole sequence per lead; in the Smartlead campaign
create the steps as

    step 1   subject: {{subject_1}}   body: {{email_1}}
    step 2   subject: (empty)         body: {{email_2}}      <- empty subject = same thread
    ...

``{{personalization}}``, ``{{job_title}}``, ``{{signal}}``, ``{{score}}`` and
``{{tier}}`` are custom fields too (``subject_<k>`` appears for follow-ups
that start a new thread). Set ``body_format: html`` if line breaks collapse.

``type: smartlead_csv`` -> ``smartlead_upload.csv`` (offline)
-------------------------------------------------------------
Columns: ``email, first_name, last_name, company_name, website, location,
phone_number, linkedin_profile, company_url`` (Smartlead's standard lead
fields) followed by the custom-field columns ``personalization, job_title,
subject_1, email_1, email_2 .. email_N, signal, score, tier``. Leads without
an email are skipped. Config: ``filename``, ``encoding``, ``formula_guard``,
``body_format``, ``clean_company_name``, ``label`` (see
``leadgen.outbound.csv_export.UploadCsvExporter``).

``type: smartlead`` - push leads through the API (``is_send``)
--------------------------------------------------------------
``POST {base_url}/campaigns/{campaign_id}/leads?api_key=<SMARTLEAD_API_KEY>``
in batches of ``batch_size`` (default 100, Smartlead's per-request maximum)
with JSON::

    {"lead_list": [{"first_name": "Jane", "last_name": "Doe", "email": "jane@acme.com",
                    "phone_number": "...", "company_name": "Acme",
                    "website": "https://acme.com", "location": "London, UK",
                    "linkedin_profile": "...", "company_url": "https://acme.com",
                    "custom_fields": {"personalization": "...", "job_title": "CFO",
                                      "subject_1": "...", "email_1": "...", "email_2": "...",
                                      "signal": "...", "score": "86", "tier": "hot"}}],
     "settings": {"ignore_global_block_list": false,
                  "ignore_unsubscribe_list": false,
                  "ignore_duplicate_leads_in_other_campaign": false}}

Custom field values are sent as strings. Documented response::

    {"ok": true, "upload_count": 2, "total_leads": 2, "already_added_to_campaign": 0,
     "duplicate_count": 0, "invalid_email_count": 0, "unsubscribed_leads": [],
     "is_lead_limit_exhausted": false, "lead_import_stopped_count": 0}

A lead whose email already appeared earlier in the same upload (the same
person under two company records) is skipped; the highest-scored one is sent.

The per-batch numbers are summed into ``detail`` (e.g. ``uploaded 5/6 to
campaign 123; already in campaign 1``). A batch counts as handed over when
the call succeeds and ``ok`` is not false; addresses Smartlead lists back as
rejected (any list of emails / ``{"email": ...}`` objects under
``rejected_keys``) are left out of ``exported_ids``. When
``is_lead_limit_exhausted`` is true the batch is not marked (Smartlead dedupes
by ``already_added_to_campaign`` on the next run) and no further batches are
sent. A failed batch (HTTP error / ``ok: false``) is logged and its leads are
excluded from ``exported_ids``. HTTP 401 / 403 / 404 (bad key, no access,
unknown campaign) stop the push: ``SmartleadError`` if nothing was uploaded,
else a partial result with the reason in ``detail``.

In ``ctx.dry_run`` no request is made: ``count=0, detail="dry-run"``.

Credential: ``SMARTLEAD_API_KEY`` (or config ``api_key`` / ``api_key_env``),
sent as the ``api_key`` query parameter, resolved at the first real push.
Every error text this exporter logs, raises or puts in ``detail`` is scrubbed
of the key first (a network error's text carries the full request URL,
query string included).

Config keys (``type: smartlead``)
---------------------------------
campaign_id         Required. The numeric Smartlead campaign id.
batch_size          Leads per request (default 100).
settings            Dict merged over the default ``settings`` above.
base_url            Default ``https://server.smartlead.ai/api/v1``.
leads_path          Default ``/campaigns/{campaign_id}/leads``.
rejected_keys       Response keys that may list rejected leads (default
                    ``unsubscribed_leads, invalid_emails, invalid_email_leads,
                    blocked_leads, bounced_leads``).
body_format         ``text`` (default) | ``html``.
clean_company_name  Default true (``Acme Inc.`` -> ``Acme``).
timeout             HTTP timeout in seconds (default 60).
label               Name reported in ``ExportResult.exporter`` (default ``smartlead``).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

from ..http import HttpError
from ..models import Lead
from ..utils import chunks, to_int
from .base import Exporter, ExportResult
from .csv_export import (BODY_FORMATS, FATAL_STATUSES, UploadCsvExporter, _bool, config_int,
                         followup_subject_steps, max_steps, outbound_row, response_error,
                         sequence_keys, sorted_by_score, unique_by_email)

DEFAULT_BASE_URL = "https://server.smartlead.ai/api/v1"
DEFAULT_LEADS_PATH = "/campaigns/{campaign_id}/leads"
DEFAULT_SETTINGS: Dict[str, Any] = {
    "ignore_global_block_list": False,
    "ignore_unsubscribe_list": False,
    "ignore_duplicate_leads_in_other_campaign": False,
}
DEFAULT_REJECTED_KEYS = ("unsubscribed_leads", "invalid_emails", "invalid_email_leads",
                         "blocked_leads", "bounced_leads")
#: ``api_key=<value>`` inside an error text (value ends at & / whitespace / quote / bracket).
_KEY_QS = re.compile(r"(api_?key=)[^&\s'\")]+", re.I)
#: Smartlead documents a cap on custom fields per lead.
MAX_CUSTOM_FIELDS = 20

_CUSTOM_HEAD = ("personalization", "job_title")
_CUSTOM_TAIL = ("signal", "score", "tier")
# response counters summed across batches -> label used in ``detail``
_COUNTERS = (
    ("already_added_to_campaign", "already in campaign"),
    ("duplicate_count", "duplicates"),
    ("invalid_email_count", "invalid emails"),
    ("unsubscribed_leads", "unsubscribed"),
    ("block_count", "blocked"),
    ("bounce_count", "bounced"),
    ("lead_import_stopped_count", "import stopped"),
)


class SmartleadError(RuntimeError):
    """Smartlead rejected the push as a whole (bad key, unknown campaign, API down)."""


def _count(value: Any) -> int:
    """Response counters may be ints, numeric strings or lists of leads."""
    if isinstance(value, (list, tuple)):
        return len(value)
    n = to_int(value)
    return n or 0


def scrub(text: str, secret: str) -> str:
    """``text`` without ``secret`` or any ``api_key=...`` query value (safe to log / raise).

    Only the value is masked, so e.g. a network error's cause ("Connection
    refused") stays readable.
    """
    text = str(text or "")
    if secret:
        text = text.replace(secret, "***")
    return _KEY_QS.sub(r"\1***", text)


def _emails_in(value: Any) -> Set[str]:
    out: Set[str] = set()
    if isinstance(value, (list, tuple)):
        for v in value:
            if isinstance(v, str) and "@" in v:
                out.add(v.strip().lower())
            elif isinstance(v, dict):
                e = v.get("email") or v.get("lead_email")
                if isinstance(e, str) and e:
                    out.add(e.strip().lower())
    return out


class SmartleadCsvExporter(UploadCsvExporter):
    """Write ``smartlead_upload.csv`` for Smartlead's CSV lead import (see module docstring)."""

    name = "smartlead_csv"
    default_filename = "smartlead_upload.csv"
    standard_columns = (
        ("email", "email"), ("first_name", "first_name"), ("last_name", "last_name"),
        ("company_name", "company_name"), ("website", "website"), ("location", "location"),
        ("phone_number", "phone"), ("linkedin_profile", "linkedin_url"),
        ("company_url", "website"),
    )
    custom_keys = _CUSTOM_HEAD


class SmartleadApiExporter(Exporter):
    """Upload leads (with their sequence as custom fields) to a Smartlead campaign.

    See the module docstring for the request/response shapes, batching,
    error handling and every config key (``campaign_id`` is required).
    """

    name = "smartlead"
    env_key = "SMARTLEAD_API_KEY"
    scope = "outbound"
    is_send = True
    offline = False

    # --- config ---------------------------------------------------------------------
    @property
    def label(self) -> str:
        return str(self.config.get("label") or self.name)

    @property
    def campaign_id(self) -> str:
        cid = str(self.config.get("campaign_id") or "").strip()
        if not cid:
            raise ValueError("smartlead: 'campaign_id' is required - copy the numeric campaign id "
                             "from the Smartlead campaign URL into outbound.exporters[].campaign_id")
        return cid

    def url(self, campaign_id: str) -> str:
        base = str(self.config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        path = str(self.config.get("leads_path") or DEFAULT_LEADS_PATH)
        return base + "/" + path.replace("{campaign_id}", campaign_id).lstrip("/")

    @property
    def settings(self) -> Dict[str, Any]:
        s = dict(DEFAULT_SETTINGS)
        extra = self.config.get("settings")
        if isinstance(extra, dict):
            s.update(extra)
        return s

    def _body_format(self) -> str:
        fmt = str(self.config.get("body_format") or "text").lower()
        if fmt not in BODY_FORMATS:
            raise ValueError(f"smartlead: body_format must be one of {', '.join(BODY_FORMATS)}, got {fmt!r}")
        return fmt

    # --- payload --------------------------------------------------------------------
    def lead_item(self, lead: Lead, steps: int, subject_steps: Sequence[int] = (),
                  body_format: str = "text") -> Dict[str, Any]:
        """One ``lead_list`` entry for ``lead``."""
        row = outbound_row(lead, steps, subject_steps, body_format,
                           clean_company=_bool(self.config.get("clean_company_name"), True))
        keys = list(_CUSTOM_HEAD) + sequence_keys(steps, subject_steps) + list(_CUSTOM_TAIL)
        custom = {k: "" if row.get(k) is None else str(row.get(k)) for k in keys}
        return {
            "first_name": row["first_name"],
            "last_name": row["last_name"],
            "email": row["email"],
            "phone_number": row["phone"],
            "company_name": row["company_name"],
            "website": row["website"],
            "location": row["location"],
            "linkedin_profile": row["linkedin_url"],
            "company_url": row["website"],
            "custom_fields": custom,
        }

    # --- export ---------------------------------------------------------------------
    def export(self, leads: List[Lead], out_dir: Path) -> ExportResult:
        ordered = sorted_by_score(leads)
        if self.ctx.dry_run:
            self.log.info("smartlead: dry-run - not uploading %d lead(s) to campaign %s",
                          len(ordered), self.config.get("campaign_id") or "(campaign_id not set)")
            return ExportResult(exporter=self.label, count=0, detail="dry-run")
        campaign = self.campaign_id
        fmt = self._body_format()
        with_email, dups = unique_by_email(ld for ld in ordered if ld.contact and ld.contact.email)
        skipped = len(ordered) - len(with_email) - len(dups)
        if dups:
            self.log.warning("smartlead: skipped %d lead(s) whose email is already in this upload",
                             len(dups))
        if not with_email:
            detail = "no leads to upload" + (f" ({skipped} skipped: no email)" if skipped else "")
            return ExportResult(exporter=self.label, count=0, detail=detail)
        api_key = self.secret()
        batch_size = config_int(self.config, "batch_size", 100)
        timeout = float(config_int(self.config, "timeout", 60))
        rejected_keys = self.config.get("rejected_keys") or DEFAULT_REJECTED_KEYS
        steps = max(1, max_steps(with_email))
        subject_steps = followup_subject_steps(with_email)
        url = self.url(campaign)
        settings = self.settings

        exported: List[str] = []
        totals: Dict[str, int] = {k: 0 for k, _ in _COUNTERS}
        uploaded = sent = failed_batches = failed_leads = 0
        limit_hit = False
        abort: Optional[str] = None
        batches = list(chunks(with_email, batch_size))
        for bi, batch in enumerate(batches, 1):
            items = [self.lead_item(ld, steps, subject_steps, fmt) for ld in batch]
            if bi == 1 and items and len(items[0]["custom_fields"]) > MAX_CUSTOM_FIELDS:
                self.log.warning("smartlead: %d custom fields per lead (Smartlead allows %d); "
                                 "shorten writer.sequence if the upload rejects them",
                                 len(items[0]["custom_fields"]), MAX_CUSTOM_FIELDS)
            sent += len(batch)
            body = {"lead_list": items, "settings": settings}
            try:
                resp = self.http.request("POST", url, params={"api_key": api_key}, json=body,
                                         timeout=timeout, raise_for_status=False)
                status, ok = resp.status, resp.ok
                err = "" if ok else response_error(resp)
            except HttpError as e:  # network failure after retries: its text contains the URL
                resp, status, ok, err = None, e.status, False, e.body or str(e)
            err = scrub(err, api_key)
            data: Dict[str, Any] = {}
            if ok and resp is not None:
                try:
                    parsed = resp.json()
                except (ValueError, TypeError):
                    parsed = None
                data = parsed if isinstance(parsed, dict) else {}
                if data.get("ok") is False:
                    ok, err = False, scrub(response_error(resp), api_key)
            if not ok:
                failed_batches += 1
                failed_leads += len(batch)
                self.log.warning("smartlead: batch %d/%d (%d leads) failed - HTTP %s: %s",
                                 bi, len(batches), len(batch), status, err)
                if status in FATAL_STATUSES:
                    hint = {401: "check SMARTLEAD_API_KEY", 403: "the API key has no access to this campaign",
                            404: f"campaign {campaign!r} or endpoint not found"}.get(status, "")
                    abort = f"HTTP {status}: {err}" + (f" ({hint})" if hint else "")
                    break
                continue

            uploaded += _count(data.get("upload_count")) if "upload_count" in data else len(batch)
            for key, _ in _COUNTERS:
                totals[key] += _count(data.get(key))
            if data.get("is_lead_limit_exhausted"):
                limit_hit = True
                self.log.warning("smartlead: the account's lead limit is exhausted - batch %d not "
                                 "marked as exported and remaining batches not sent", bi)
                break
            rejected: Set[str] = set()
            for key in rejected_keys:
                rejected |= _emails_in(data.get(key))
            for ld in batch:
                if ld.contact and ld.contact.email.lower() not in rejected:
                    exported.append(ld.id)

        if abort and not exported and not uploaded:
            raise SmartleadError(f"smartlead: upload to campaign {campaign} stopped - {abort}")
        parts = [f"uploaded {uploaded}/{sent} to campaign {campaign}"]
        parts += [f"{label} {totals[key]}" for key, label in _COUNTERS if totals[key]]
        if failed_batches:
            parts.append(f"{failed_batches} failed batch(es) ({failed_leads} leads)")
        if skipped:
            parts.append(f"{skipped} skipped (no email)")
        if dups:
            parts.append(f"{len(dups)} skipped (duplicate email)")
        if limit_hit:
            parts.append("lead limit exhausted")
        not_sent = len(with_email) - sent
        if abort:
            parts.append(f"stopped: {abort}")
            self.log.error("smartlead: stopped early - %s", abort)
        if not_sent > 0:
            parts.append(f"{not_sent} not sent")
        detail = "; ".join(parts)
        self.log.info("smartlead: %s", detail)
        return ExportResult(exporter=self.label, count=len(exported), detail=detail,
                            exported_ids=exported)


__all__ = ["SmartleadCsvExporter", "SmartleadApiExporter", "SmartleadError", "scrub"]
