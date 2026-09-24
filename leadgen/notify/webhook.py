"""Generic webhook notifier (``type: webhook``): POST alerts as JSON anywhere.

Use it to feed Zapier / Make / n8n / a CRM / your own endpoint.

Request: ``POST <url>`` (method configurable) with JSON::

    {"event": "positive",            # positive | question | referral | run_summary | ...
     "title": "Positive reply: Jane Doe (Acme)",
     "text":  "From: Jane Doe <jane@acme.com> ...",
     "data":  {...}}                  # event-specific structured payload

``data`` is made JSON-safe first (dates, sets and other objects become
strings/lists). Any 2xx response counts as delivered; anything else raises
``WebhookError``. The URL may carry a secret (token in the path or query), so
it never appears in logs or raised errors - only its scheme + host.

In ``ctx.dry_run`` ``send`` only logs what it would have posted.

Credential
----------
``url`` in the channel config, or the env var named by ``url_env``, or
``LEADGEN_WEBHOOK_URL``. Resolved lazily at the first ``send``.

Config keys
-----------
url           Endpoint to call.
url_env       Name of the env var holding the URL (default ``LEADGEN_WEBHOOK_URL``).
headers       Optional dict of extra HTTP headers (e.g. an auth token; values
              may use ``${ENV_VAR}`` - the playbook expands them).
method        ``POST`` (default), ``PUT`` or ``PATCH``.
include_data  Send the ``data`` object (default true); false sends ``{}``.
events        Optional list of event names this channel accepts; others are
              silently ignored.
timeout       HTTP timeout in seconds (default 15).
"""
from __future__ import annotations

import json
from typing import Any, Dict
from urllib.parse import urlparse

from ..http import HttpError
from .base import Notifier

_METHODS = ("POST", "PUT", "PATCH")


class WebhookError(RuntimeError):
    """The endpoint rejected the event (or could not be reached)."""


def _safe_host(url: str) -> str:
    try:
        p = urlparse(url)
        return f"{p.scheme}://{p.hostname}" if p.hostname else "<webhook>"
    except ValueError:
        return "<webhook>"


def _safe_detail(e: HttpError, url: str) -> str:
    """What went wrong, without any part of the secret URL.

    A network error (status 0) carries the ``requests`` exception text, which
    embeds the request path and query ("Max retries exceeded with url:
    /hook/SECRET?sig=...") - only its exception class is kept. Any other body
    is scrubbed of the URL, its path and its query.
    """
    body = str(e.body or "")
    if not e.status:
        return body.split(":", 1)[0].strip()[:80] or "network error"
    try:
        p = urlparse(url)
        pieces = [url, p.path + (f"?{p.query}" if p.query else ""), p.path, p.query]
    except ValueError:
        pieces = [url]
    for piece in sorted((x for x in pieces if x and x != "/"), key=len, reverse=True):
        body = body.replace(piece, "<redacted>")
    return body[:200]


def jsonable(value: Any) -> Any:
    """Return a JSON-serialisable deep copy (unknown objects -> ``str``)."""
    def _default(o: Any) -> Any:
        if isinstance(o, (set, frozenset, tuple)):
            return list(o)
        to_dict = getattr(o, "to_dict", None)
        if callable(to_dict):
            return to_dict()
        return str(o)

    return json.loads(json.dumps(value, default=_default))


class WebhookNotifier(Notifier):
    """POST ``{event, title, text, data}`` to a URL (see module docstring)."""

    name = "webhook"
    env_key = "LEADGEN_WEBHOOK_URL"

    def accepts(self, event: str) -> bool:
        events = self.config.get("events")
        return not events or event in events

    def _headers(self) -> Dict[str, str]:
        raw = self.config.get("headers") or {}
        if not isinstance(raw, dict):
            raise WebhookError("webhook: 'headers' must be a mapping of header name -> value")
        headers = {str(k): str(v) for k, v in raw.items() if v is not None}
        if not any(k.lower() == "content-type" for k in headers):
            headers["Content-Type"] = "application/json"
        return headers

    def build_payload(self, event: str, title: str, text: str, data: Dict[str, Any]) -> Dict[str, Any]:
        include = self.config.get("include_data", True) is not False
        return {"event": event, "title": title, "text": text,
                "data": jsonable(data or {}) if include else {}}

    def send(self, event: str, title: str, text: str, data: Dict[str, Any]) -> None:
        if not self.accepts(event):
            return
        if self.ctx.dry_run:
            self.log.info("dry-run: not posting '%s' event to webhook", event)
            return
        url = self.secret("url", "LEADGEN_WEBHOOK_URL").strip()
        if not url.lower().startswith(("https://", "http://")):
            raise WebhookError("webhook: 'url' must be an http(s) URL")
        method = str(self.config.get("method") or "POST").upper()
        if method not in _METHODS:
            raise WebhookError(f"webhook: method must be one of {', '.join(_METHODS)}, got {method!r}")
        try:
            timeout = float(self.config.get("timeout") or 15)
        except (TypeError, ValueError):
            timeout = 15.0
        payload = self.build_payload(event, title, text, data)
        try:
            resp = self.http.request(method, url, json=payload, headers=self._headers(),
                                     timeout=timeout, raise_for_status=False)
        except HttpError as e:
            raise WebhookError(f"webhook: could not reach {_safe_host(url)} "
                               f"(HTTP {e.status}): {_safe_detail(e, url)}") from None
        if not resp.ok:
            body = (resp.text or "").strip()[:200] or "no response body"
            raise WebhookError(f"webhook: {_safe_host(url)} returned HTTP {resp.status}: {body}")
        self.log.debug("webhook: delivered '%s' event to %s", event, _safe_host(url))


__all__ = ["WebhookNotifier", "WebhookError", "jsonable"]
