"""Webhook exporter (``type: webhook``): POST send-eligible leads as JSON to any URL.

For Make / n8n / Zapier / Clay / a CRM / your own endpoint. Receives only
send-eligible leads (``scope = "outbound"``, ``is_send``); what is delivered
is marked EXPORTED by the pipeline so it is never handed over twice.

Payloads
--------
Batches (default; ``batch_size`` leads per request, default 50)::

    {"playbook": "my-playbook", "batch": 1, "batches": 3, "count": 50,
     "leads": [<lead>, ...]}

One request per lead (``per_lead: true`` - simplest for Zapier / Make)::

    {"playbook": "my-playbook", "lead": <lead>}

``<lead>`` is ``Lead.to_dict()`` (company, contact, signals, score breakdown,
messages ...) plus, unless ``include_flat: false``, a ``"flat"`` object with
ready-to-map scalar fields (``email, first_name, last_name, company_name,
website, job_title, linkedin_url, phone, location, personalization,
hypothesis, signal, signal_type, signal_url, score, tier, subject_1, email_1,
email_2 .. email_N`` - see ``leadgen.outbound.csv_export.outbound_row``).
Follow-up subject ``""`` means "reply in the same thread". Everything is made
JSON-safe (dates -> ISO strings).

Any 2xx response counts as delivered. A failed request is logged and its
leads are left out of ``exported_ids`` (a later run retries them). HTTP 401 /
403 / 404 / 405 / 410 stop the export (the URL or credentials are wrong):
``WebhookExportError`` if nothing was delivered, else a partial result. The URL
may carry a secret, so logs and errors only ever show its scheme + host.

In ``ctx.dry_run`` no request is made (``count=0, detail="dry-run"``).

Credential
----------
``url`` in the exporter config, or the env var named by ``url_env``, or
``LEADGEN_EXPORT_WEBHOOK_URL``; resolved at the first real export.

Config keys
-----------
url           Endpoint (http/https).
url_env       Env var holding the URL (default ``LEADGEN_EXPORT_WEBHOOK_URL``).
batch_size    Leads per request (default 50).
per_lead      One request per lead with ``{"playbook", "lead"}`` (default false).
headers       Optional dict of extra headers (e.g. ``{Authorization: "Bearer ${TOKEN}"}``).
method        POST (default), PUT or PATCH.
include_flat  Add the ``flat`` field map to every lead (default true).
clean_company_name  ``flat.company_name`` without legal suffix (default true:
              ``Acme Inc.`` -> ``Acme``; the full name stays in ``company.name``).
body_format   ``text`` (default) | ``html`` for the bodies in ``flat``.
timeout       HTTP timeout in seconds (default 30).
label         Name reported in ``ExportResult.exporter`` (default ``webhook``).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from ..http import HttpError
from ..models import Lead
from .base import Exporter, ExportResult
from .csv_export import (BODY_FORMATS, _bool, config_int, followup_subject_steps, max_steps,
                         outbound_row, response_error, sorted_by_score)

ENV_URL = "LEADGEN_EXPORT_WEBHOOK_URL"
METHODS = ("POST", "PUT", "PATCH")
FATAL = (401, 403, 404, 405, 410)


class WebhookExportError(RuntimeError):
    """The endpoint rejected the export as a whole (or is misconfigured)."""


def safe_host(url: str) -> str:
    """Scheme + host only: webhook URLs often embed a secret token in the path/query."""
    try:
        p = urlparse(url)
        return f"{p.scheme}://{p.hostname}" if p.hostname else "<webhook>"
    except ValueError:
        return "<webhook>"


def jsonable(value: Any) -> Any:
    """JSON-safe deep copy (dates / sets / unknown objects become strings or lists)."""
    def _default(o: Any) -> Any:
        if isinstance(o, (set, frozenset, tuple)):
            return list(o)
        to_dict = getattr(o, "to_dict", None)
        if callable(to_dict):
            return to_dict()
        return str(o)

    return json.loads(json.dumps(value, default=_default))


class WebhookExporter(Exporter):
    """POST leads as JSON to a URL, batched or one per request (see module docstring)."""

    name = "webhook"
    env_key = ENV_URL
    scope = "outbound"
    is_send = True
    offline = False

    @property
    def label(self) -> str:
        return str(self.config.get("label") or self.name)

    def _url(self) -> str:
        url = self.secret("url", ENV_URL).strip()
        if not url.lower().startswith(("https://", "http://")):
            raise ValueError("webhook exporter: 'url' must be an http(s) URL")
        return url

    def _method(self) -> str:
        method = str(self.config.get("method") or "POST").upper()
        if method not in METHODS:
            raise ValueError(f"webhook exporter: method must be one of {', '.join(METHODS)}, got {method!r}")
        return method

    def _headers(self) -> Dict[str, str]:
        raw = self.config.get("headers") or {}
        if not isinstance(raw, dict):
            raise ValueError("webhook exporter: 'headers' must be a mapping of header name -> value")
        headers = {str(k): str(v) for k, v in raw.items() if v is not None}
        if not any(k.lower() == "content-type" for k in headers):
            headers["Content-Type"] = "application/json"
        return headers

    def lead_payload(self, lead: Lead, steps: int, subject_steps: List[int], body_format: str) -> Dict[str, Any]:
        """``lead.to_dict()`` (+ ``flat`` field map unless ``include_flat: false``), JSON-safe."""
        d = lead.to_dict()
        if _bool(self.config.get("include_flat"), True):
            d["flat"] = outbound_row(lead, steps, subject_steps, body_format,
                                     clean_company=_bool(self.config.get("clean_company_name"), True))
        return jsonable(d)

    def export(self, leads: List[Lead], out_dir: Path) -> ExportResult:
        ordered = sorted_by_score(leads)
        if self.ctx.dry_run:
            self.log.info("webhook exporter: dry-run - not posting %d lead(s)", len(ordered))
            return ExportResult(exporter=self.label, count=0, detail="dry-run")
        if not ordered:
            return ExportResult(exporter=self.label, count=0, detail="no leads to send")
        fmt = str(self.config.get("body_format") or "text").lower()
        if fmt not in BODY_FORMATS:
            raise ValueError(f"webhook exporter: body_format must be one of {', '.join(BODY_FORMATS)}")
        url, method, headers = self._url(), self._method(), self._headers()
        host = safe_host(url)
        timeout = float(config_int(self.config, "timeout", 30))
        per_lead = _bool(self.config.get("per_lead"), False)
        size = 1 if per_lead else config_int(self.config, "batch_size", 50)
        steps = max(1, max_steps(ordered))
        subject_steps = followup_subject_steps(ordered)
        playbook = self.ctx.playbook.name
        batches = [ordered[i:i + size] for i in range(0, len(ordered), size)]

        delivered: List[str] = []
        failed = 0
        abort: Optional[str] = None
        for bi, batch in enumerate(batches, 1):
            items = [self.lead_payload(ld, steps, subject_steps, fmt) for ld in batch]
            if per_lead:
                payload: Dict[str, Any] = {"playbook": playbook, "lead": items[0]}
            else:
                payload = {"playbook": playbook, "batch": bi, "batches": len(batches),
                           "count": len(items), "leads": items}
            try:
                resp = self.http.request(method, url, json=payload, headers=headers,
                                         timeout=timeout, raise_for_status=False)
                status, ok = resp.status, resp.ok
                err = "" if ok else response_error(resp)
            except HttpError as e:
                status, ok = e.status, False
                if e.status:
                    err = (e.body or "request failed")[:200]
                else:  # network error text can contain the full URL (and its token)
                    err = "network error: " + ((e.body or "").split(":", 1)[0] or "request failed")
            if ok:
                delivered.extend(ld.id for ld in batch)
                continue
            failed += len(batch)
            self.log.warning("webhook exporter: %s returned HTTP %s for %d lead(s): %s",
                             host, status, len(batch), err)
            if status in FATAL:
                abort = f"{host} returned HTTP {status}: {err}"
                break

        if abort and not delivered:
            raise WebhookExportError(f"webhook exporter: stopped - {abort}")
        parts = [f"delivered {len(delivered)}/{len(ordered)} to {host}"]
        if failed:
            parts.append(f"{failed} failed")
        if abort:
            parts.append(f"stopped: {abort}")
            self.log.error("webhook exporter: stopped early - %s", abort)
        detail = "; ".join(parts)
        self.log.info("webhook exporter: %s", detail)
        return ExportResult(exporter=self.label, count=len(delivered), detail=detail,
                            exported_ids=delivered)


__all__ = ["WebhookExporter", "WebhookExportError", "safe_host", "jsonable"]
