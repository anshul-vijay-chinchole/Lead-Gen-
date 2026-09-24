"""Thin HTTP layer used by every adapter.

All network access goes through ``HttpClient`` so that:
  * retries/backoff on 429 + 5xx are handled in one place,
  * adapters can be unit-tested with ``tests.fakes.FakeHttp`` (same interface),
  * secrets never end up in logs or error messages: query-string keys are
    redacted and token-like path segments (Slack/Zapier/Make hook URLs) masked.
"""
from __future__ import annotations

import json as _json
import logging
import re
import time
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

import requests

log = logging.getLogger("leadgen.http")

_SECRET_QS = re.compile(r"((?:api_?key|app_?key|app_?id|token|access_token|key|api|secret)=)[^&\s'\")]+", re.I)
_BEARER = re.compile(r"(bearer\s+)[A-Za-z0-9._~+/=-]+", re.I)

# Errors that retrying cannot fix.
_NO_RETRY = (requests.exceptions.InvalidHeader, requests.exceptions.InvalidURL,
             requests.exceptions.MissingSchema, requests.exceptions.InvalidSchema)


def redact(text: str) -> str:
    """Mask ``api_key=...``-style values and bearer tokens anywhere in ``text``."""
    return _BEARER.sub(r"\1***", _SECRET_QS.sub(r"\1***", text or ""))


def _secret_like(segment: str) -> bool:
    """Tokens / account ids in URL paths (hook tokens, Slack T../B.. ids, numeric ids)."""
    return len(segment) >= 16 or (len(segment) >= 5 and any(c.isdigit() for c in segment))


def safe_url(url: str) -> str:
    """URL for logs/errors: query secrets redacted and token-like path segments masked."""
    try:
        p = urlsplit(redact(url))
    except ValueError:
        return "<url>"
    segs = ["***" if _secret_like(s) else s for s in p.path.split("/")]
    return f"{p.scheme}://{p.netloc}{'/'.join(segs)}" + (f"?{p.query}" if p.query else "")


def _scrub(message: str, url: str) -> str:
    """Remove every form of ``url`` (full, path+query, path) from an exception message."""
    msg = message or ""
    try:
        sp = urlsplit(url)
        forms = {url, sp.path + (f"?{sp.query}" if sp.query else ""), sp.path}
    except ValueError:
        forms = {url}
    for raw in sorted(forms, key=len, reverse=True):
        if raw and raw != "/":
            msg = msg.replace(raw, "<url>")
    return redact(msg)


class HttpError(RuntimeError):
    def __init__(self, status: int, url: str, body: str = ""):
        self.status = status
        self.url = safe_url(url)
        self.body = redact(body or "")[:500]
        super().__init__(f"HTTP {status} for {self.url}: {self.body}")


class Response:
    """Minimal response object (same shape as tests.fakes.FakeResponse)."""

    def __init__(self, status: int, text: str, headers: Optional[Dict[str, str]] = None):
        self.status = status
        self.text = text
        self.headers = headers or {}

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self) -> Any:
        if not self.text:
            return None
        return _json.loads(self.text)


class HttpClient:
    """requests-based client with retry + backoff.

    ``request`` raises ``HttpError`` for final non-2xx responses unless
    ``raise_for_status=False``. Network errors become ``HttpError(0, ...)``
    with the URL (and any secret in it) scrubbed from the message.
    """

    RETRY_STATUSES = (429, 500, 502, 503, 504)

    def __init__(self, timeout: float = 30.0, retries: int = 3, backoff: float = 1.5,
                 user_agent: str = "leadgen/0.1", sleep=time.sleep):
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self._sleep = sleep

    def request(self, method: str, url: str, *, params: Optional[Dict[str, Any]] = None,
                json: Any = None, data: Any = None, headers: Optional[Dict[str, str]] = None,
                timeout: Optional[float] = None, raise_for_status: bool = True) -> Response:
        shown = safe_url(url)
        attempt = 0
        while True:
            attempt += 1
            try:
                r = self.session.request(method.upper(), url, params=params, json=json, data=data,
                                         headers=headers, timeout=timeout or self.timeout)
                resp = Response(r.status_code, r.text, dict(r.headers))
            except requests.RequestException as e:
                if isinstance(e, requests.exceptions.InvalidHeader):
                    detail = type(e).__name__  # its message quotes the header value (the key)
                else:
                    detail = f"{type(e).__name__}: {_scrub(str(e), url)}"
                if attempt > self.retries or isinstance(e, _NO_RETRY):
                    raise HttpError(0, url, detail) from None
                wait = self.backoff ** attempt
                log.warning("network error on %s %s (%s); retry %d in %.1fs",
                            method, shown, type(e).__name__, attempt, wait)
                self._sleep(wait)
                continue
            if resp.status in self.RETRY_STATUSES and attempt <= self.retries:
                retry_after = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
                try:
                    wait = min(60.0, float(retry_after)) if retry_after else self.backoff ** attempt
                except ValueError:
                    wait = self.backoff ** attempt
                log.warning("HTTP %d on %s %s; retry %d in %.1fs", resp.status, method, shown, attempt, wait)
                self._sleep(wait)
                continue
            if raise_for_status and not resp.ok:
                raise HttpError(resp.status, url, resp.text)
            return resp

    def get(self, url: str, **kw: Any) -> Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw: Any) -> Response:
        return self.request("POST", url, **kw)

    def get_json(self, url: str, **kw: Any) -> Any:
        return self.request("GET", url, **kw).json()

    def post_json(self, url: str, **kw: Any) -> Any:
        return self.request("POST", url, **kw).json()
