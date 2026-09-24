"""Thin HTTP layer used by every adapter.

All network access goes through ``HttpClient`` so that:
  * retries/backoff on 429 + 5xx are handled in one place,
  * adapters can be unit-tested with ``tests.fakes.FakeHttp`` (same interface),
  * secrets never end up in logs (query strings are redacted).
"""
from __future__ import annotations

import json as _json
import logging
import re
import time
from typing import Any, Dict, Optional

import requests

log = logging.getLogger("leadgen.http")

_SECRET_QS = re.compile(r"((?:api_?key|app_key|token|key|api)=)[^&]+", re.I)


def redact(url: str) -> str:
    return _SECRET_QS.sub(r"\1***", url)


class HttpError(RuntimeError):
    def __init__(self, status: int, url: str, body: str = ""):
        self.status = status
        self.url = redact(url)
        self.body = (body or "")[:500]
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
    ``raise_for_status=False``.
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
        attempt = 0
        while True:
            attempt += 1
            try:
                r = self.session.request(method.upper(), url, params=params, json=json, data=data,
                                         headers=headers, timeout=timeout or self.timeout)
                resp = Response(r.status_code, r.text, dict(r.headers))
            except requests.RequestException as e:
                if attempt > self.retries:
                    raise HttpError(0, url, f"{type(e).__name__}: {e}") from e
                wait = self.backoff ** attempt
                log.warning("network error on %s %s (%s); retry %d in %.1fs",
                            method, redact(url), type(e).__name__, attempt, wait)
                self._sleep(wait)
                continue
            if resp.status in self.RETRY_STATUSES and attempt <= self.retries:
                retry_after = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
                try:
                    wait = min(60.0, float(retry_after)) if retry_after else self.backoff ** attempt
                except ValueError:
                    wait = self.backoff ** attempt
                log.warning("HTTP %d on %s %s; retry %d in %.1fs",
                            resp.status, method, redact(url), attempt, wait)
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
