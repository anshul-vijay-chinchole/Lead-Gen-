"""Instantly.ai: upload CSV (``type: instantly_csv``) and API v2 sender (``type: instantly``).

Both receive only send-eligible leads (``scope = "outbound"``) and the
pipeline marks what they hand over as EXPORTED, so the same person is never
handed over twice.

Setting up the Instantly campaign
---------------------------------
The engine writes the whole sequence per lead; the campaign just renders it.
In Instantly create the sequence steps with these variables:

    step 1   subject: {{subject_1}}   body: {{email_1}}
    step 2   subject: (leave empty)   body: {{email_2}}     <- empty subject = same thread
    step 3   subject: (leave empty)   body: {{email_3}}
    ...      (match the delays to writer.sequence days, e.g. 1 / 3 / 7 / 12)

A follow-up whose writer output carries its own subject (a new thread) also
gets a ``subject_<k>`` variable. ``{{personalization}}``, ``{{signal}}``,
``{{score}}``, ``{{tier}}``, ``{{job_title}}``, ``{{linkedin_url}}``,
``{{location}}`` are available too. If line breaks disappear inside variables
in your campaign, set ``body_format: html`` (bodies then use ``<br>``).

``type: instantly_csv`` -> ``instantly_upload.csv`` (offline)
-------------------------------------------------------------
Columns: ``email, first_name, last_name, company_name, website,
personalization, job_title, linkedin_url, phone, location`` followed by the
custom-variable columns ``subject_1, email_1, email_2 .. email_N, signal,
score, tier``. On upload, map the first block to Instantly's lead fields and
keep the rest as custom variables (``{{email_1}}`` etc.). Leads without an
email are skipped. Config: ``filename``, ``encoding``, ``formula_guard``,
``body_format``, ``clean_company_name``, ``label`` (see
``leadgen.outbound.csv_export.UploadCsvExporter``).

``type: instantly`` - push leads through the API (``is_send``)
--------------------------------------------------------------
For each lead: ``POST {base_url}/api/v2/leads`` with header
``Authorization: Bearer <INSTANTLY_API_KEY>`` and JSON::

    {"campaign": "<campaign_id>", "email": "jane@acme.com",
     "first_name": "Jane", "last_name": "Doe", "company_name": "Acme",
     "website": "https://acme.com", "personalization": "...", "phone": "...",
     "skip_if_in_workspace": true, "skip_if_in_campaign": true,
     "custom_variables": {"subject_1": "...", "email_1": "...", "email_2": "...",
                          "signal": "...", "score": 86, "tier": "hot",
                          "job_title": "CFO", "linkedin_url": "...", "location": "..."}}

Empty optional top-level fields are omitted. Custom variable values are
always scalars (str / number / bool / null) as Instantly requires. Any 2xx
response counts as added (with ``skip_if_in_*`` Instantly may silently skip a
duplicate; that still counts as handed over, which is what dedupe wants).

A lead whose email already appeared earlier in the same push (the same
person under two company records) is skipped; the highest-scored one is sent.

Per-lead failures (e.g. HTTP 400 for a rejected address) are logged and
counted in ``detail``; they are not fatal and those leads are left out of
``exported_ids`` so a later run retries them. HTTP 401 / 403 / 404 (bad key,
no access, unknown campaign) or ``max_consecutive_failures`` failures in a
row stop the push: if nothing was added yet ``InstantlyError`` is raised,
otherwise the partial result is returned with the reason in ``detail``.

In ``ctx.dry_run`` no request is made: ``count=0, detail="dry-run"``.

Credential: ``INSTANTLY_API_KEY`` (or config ``api_key`` / ``api_key_env``),
an Instantly **API v2** key, resolved at the first real push.

Config keys (``type: instantly``)
---------------------------------
campaign_id               Required. The Instantly campaign UUID.
skip_if_in_workspace      Default true: don't add someone already anywhere in the workspace.
skip_if_in_campaign       Default true: don't add someone already in this campaign.
base_url                  Default ``https://api.instantly.ai``.
leads_path                Default ``/api/v2/leads`` (override if Instantly moves it).
extra_fields              Optional dict merged into every request body (e.g.
                          ``{verify_leads_on_import: true}``); cannot override
                          ``campaign`` / ``email``.
body_format               ``text`` (default) | ``html``.
clean_company_name        Default true (``Acme Inc.`` -> ``Acme``).
max_consecutive_failures  Stop after this many failures in a row (default 10).
timeout                   HTTP timeout in seconds (default 30).
label                     Name reported in ``ExportResult.exporter`` (default ``instantly``).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..http import HttpError
from ..models import Lead
from .base import Exporter, ExportResult
from .csv_export import (BODY_FORMATS, FATAL_STATUSES, UploadCsvExporter, _bool, config_int,
                         followup_subject_steps, max_steps, outbound_row, response_error,
                         sequence_keys, sorted_by_score, unique_by_email)

DEFAULT_BASE_URL = "https://api.instantly.ai"
DEFAULT_LEADS_PATH = "/api/v2/leads"

#: outbound_row keys sent as top-level Instantly lead fields (omitted when empty).
_TOP_LEVEL = ("first_name", "last_name", "company_name", "website", "personalization", "phone")
#: outbound_row keys sent as custom variables after the sequence variables.
_CUSTOM_TAIL = ("signal", "score", "tier", "job_title", "linkedin_url", "location")


class InstantlyError(RuntimeError):
    """Instantly rejected the push as a whole (bad key, unknown campaign, API down)."""


def scalar(value: Any) -> Any:
    """Instantly custom variables must be str / number / bool / null."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return str(value)


class InstantlyCsvExporter(UploadCsvExporter):
    """Write ``instantly_upload.csv`` for Instantly's CSV lead import (see module docstring)."""

    name = "instantly_csv"
    default_filename = "instantly_upload.csv"
    standard_columns = (
        ("email", "email"), ("first_name", "first_name"), ("last_name", "last_name"),
        ("company_name", "company_name"), ("website", "website"),
        ("personalization", "personalization"), ("job_title", "job_title"),
        ("linkedin_url", "linkedin_url"), ("phone", "phone"), ("location", "location"),
    )
    custom_keys = ()


class InstantlyApiExporter(Exporter):
    """Add leads (with their full sequence as custom variables) to an Instantly campaign.

    See the module docstring for the request shape, error handling and every
    config key (``campaign_id`` is required).
    """

    name = "instantly"
    env_key = "INSTANTLY_API_KEY"
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
            raise ValueError("instantly: 'campaign_id' is required - copy the campaign ID from the "
                             "Instantly campaign URL into outbound.exporters[].campaign_id")
        return cid

    @property
    def url(self) -> str:
        base = str(self.config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        path = str(self.config.get("leads_path") or DEFAULT_LEADS_PATH)
        return base + "/" + path.lstrip("/")

    def _body_format(self) -> str:
        fmt = str(self.config.get("body_format") or "text").lower()
        if fmt not in BODY_FORMATS:
            raise ValueError(f"instantly: body_format must be one of {', '.join(BODY_FORMATS)}, got {fmt!r}")
        return fmt

    # --- payload --------------------------------------------------------------------
    def build_payload(self, lead: Lead, campaign_id: str, steps: int,
                      subject_steps: Sequence[int] = (), body_format: str = "text") -> Dict[str, Any]:
        """The JSON body for ``POST /api/v2/leads`` for one lead."""
        row = outbound_row(lead, steps, subject_steps, body_format,
                           clean_company=_bool(self.config.get("clean_company_name"), True))
        payload: Dict[str, Any] = {}
        extra = self.config.get("extra_fields")
        if isinstance(extra, dict):
            payload.update(extra)
        payload.update({
            "campaign": campaign_id,
            "email": row["email"],
            "skip_if_in_workspace": _bool(self.config.get("skip_if_in_workspace"), True),
            "skip_if_in_campaign": _bool(self.config.get("skip_if_in_campaign"), True),
        })
        for key in _TOP_LEVEL:
            if row.get(key):
                payload[key] = row[key]
        custom = {k: scalar(row.get(k, "")) for k in sequence_keys(steps, subject_steps)}
        for k in _CUSTOM_TAIL:
            custom[k] = scalar(row.get(k, ""))
        payload["custom_variables"] = custom
        return payload

    # --- export ---------------------------------------------------------------------
    def export(self, leads: List[Lead], out_dir: Path) -> ExportResult:
        ordered, dups = unique_by_email(sorted_by_score(leads))
        if self.ctx.dry_run:
            self.log.info("instantly: dry-run - not adding %d lead(s) to campaign %s",
                          len(ordered), self.config.get("campaign_id") or "(campaign_id not set)")
            return ExportResult(exporter=self.label, count=0, detail="dry-run")
        campaign = self.campaign_id
        fmt = self._body_format()
        if dups:
            self.log.warning("instantly: skipped %d lead(s) whose email is already in this push",
                             len(dups))
        if not ordered:
            return ExportResult(exporter=self.label, count=0, detail="no leads to add")
        headers = {"Authorization": f"Bearer {self.secret()}", "Content-Type": "application/json"}
        timeout = float(config_int(self.config, "timeout", 30))
        max_fail = config_int(self.config, "max_consecutive_failures", 10)
        steps = max(1, max_steps(ordered))
        subject_steps = followup_subject_steps(ordered)
        url = self.url

        added: List[str] = []
        failed = skipped = consecutive = 0
        abort: Optional[str] = None
        for i, lead in enumerate(ordered):
            payload = self.build_payload(lead, campaign, steps, subject_steps, fmt)
            email = payload["email"]
            if not email:
                skipped += 1
                continue
            try:
                resp = self.http.request("POST", url, json=payload, headers=headers,
                                         timeout=timeout, raise_for_status=False)
                status, ok = resp.status, resp.ok
                err = "" if ok else response_error(resp)
            except HttpError as e:  # network failure after retries
                status, ok, err = e.status, False, e.body or str(e)
            if ok:
                added.append(lead.id)
                consecutive = 0
                continue
            failed += 1
            consecutive += 1
            self.log.warning("instantly: could not add %s (%s) - HTTP %s: %s",
                             email, lead.company.name, status, err)
            if status in FATAL_STATUSES:
                hint = {401: "check INSTANTLY_API_KEY (an API v2 key is required)",
                        403: "the API key lacks access to leads/campaigns",
                        404: f"campaign {campaign!r} or endpoint not found"}.get(status, "")
                abort = f"HTTP {status}: {err}" + (f" ({hint})" if hint else "")
            elif consecutive >= max_fail:
                abort = f"{consecutive} failures in a row (last: HTTP {status}: {err})"
            if abort:
                remaining = len(ordered) - i - 1
                if remaining:
                    abort += f"; {remaining} lead(s) not attempted"
                break

        if abort and not added:
            raise InstantlyError(f"instantly: push to campaign {campaign} stopped - {abort}")
        parts = [f"added {len(added)}/{len(ordered)} to campaign {campaign}"]
        if failed:
            parts.append(f"{failed} failed")
        if skipped:
            parts.append(f"{skipped} skipped (no email)")
        if dups:
            parts.append(f"{len(dups)} skipped (duplicate email)")
        if abort:
            parts.append(f"stopped: {abort}")
            self.log.error("instantly: stopped early - %s", abort)
        detail = "; ".join(parts)
        self.log.info("instantly: %s", detail)
        return ExportResult(exporter=self.label, count=len(added), detail=detail, exported_ids=added)


__all__ = ["InstantlyCsvExporter", "InstantlyApiExporter", "InstantlyError", "scalar"]
