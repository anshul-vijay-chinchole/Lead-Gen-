"""Test doubles. ``FakeHttp`` has the same interface as ``leadgen.http.HttpClient``."""
from __future__ import annotations

import json as _json
import re
from typing import Any, Callable, Dict, List, Optional, Union

from leadgen.http import HttpError


class FakeResponse:
    def __init__(self, status: int = 200, body: Any = None, headers: Optional[Dict[str, str]] = None):
        self.status = status
        self.text = body if isinstance(body, str) else ("" if body is None else _json.dumps(body))
        self.headers = headers or {}

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self) -> Any:
        return _json.loads(self.text) if self.text else None


class FakeHttp:
    """Route requests to canned responses and record every call.

    http.add("GET", "https://api.x.com/v1/things", json={...})
    http.add("POST", re.compile(r"/people/match"), fn=lambda call: {"person": ...})
    http.add("GET", "https://api.x.com/v1/fail", status=500, json={"error": "boom"})

    ``fn(call)`` may return a body (status 200) or a ``(status, body)`` tuple.
    Routes match on method + URL prefix (str) or regex search (compiled pattern).
    ``times`` limits how often a route may match (then the next route is tried).
    Unmatched requests raise AssertionError so tests notice unexpected calls.
    """

    def __init__(self) -> None:
        self.routes: List[Dict[str, Any]] = []
        self.calls: List[Dict[str, Any]] = []

    def add(self, method: str, url: Union[str, "re.Pattern[str]"], *, json: Any = None,
            text: Optional[str] = None, status: int = 200, fn: Optional[Callable[[Dict[str, Any]], Any]] = None,
            headers: Optional[Dict[str, str]] = None, times: Optional[int] = None) -> "FakeHttp":
        self.routes.append({"method": method.upper(), "url": url, "json": json, "text": text,
                            "status": status, "fn": fn, "headers": headers or {}, "times": times})
        return self

    def _match(self, route: Dict[str, Any], method: str, url: str) -> bool:
        if route["method"] != method:
            return False
        if route["times"] is not None and route["times"] <= 0:
            return False
        pat = route["url"]
        if isinstance(pat, str):
            return url.startswith(pat)
        return bool(pat.search(url))

    def request(self, method: str, url: str, *, params: Optional[Dict[str, Any]] = None, json: Any = None,
                data: Any = None, headers: Optional[Dict[str, str]] = None, timeout: Optional[float] = None,
                raise_for_status: bool = True) -> FakeResponse:
        method = method.upper()
        call = {"method": method, "url": url, "params": dict(params or {}), "json": json,
                "data": data, "headers": dict(headers or {})}
        self.calls.append(call)
        for route in self.routes:
            if not self._match(route, method, url):
                continue
            if route["times"] is not None:
                route["times"] -= 1
            status, body = route["status"], route["json"] if route["text"] is None else route["text"]
            if route["fn"] is not None:
                out = route["fn"](call)
                if isinstance(out, tuple) and len(out) == 2 and isinstance(out[0], int):
                    status, body = out
                else:
                    body = out
            resp = FakeResponse(status, body, route["headers"])
            if raise_for_status and not resp.ok:
                raise HttpError(resp.status, url, resp.text)
            return resp
        raise AssertionError(f"unexpected HTTP request: {method} {url} params={params} json={json}")

    def get(self, url: str, **kw: Any) -> FakeResponse:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw: Any) -> FakeResponse:
        return self.request("POST", url, **kw)

    def get_json(self, url: str, **kw: Any) -> Any:
        return self.request("GET", url, **kw).json()

    def post_json(self, url: str, **kw: Any) -> Any:
        return self.request("POST", url, **kw).json()

    def calls_to(self, fragment: str) -> List[Dict[str, Any]]:
        return [c for c in self.calls if fragment in c["url"]]


class FakeLLM:
    """Stands in for an LLMClient: returns queued responses (str or dict) in order."""

    name = "fake-llm"
    model = "fake-model"

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    def complete(self, system: str, user: str, *, json_mode: bool = False, max_tokens: int = 1500,
                 temperature: Optional[float] = None) -> str:
        self.calls.append({"system": system, "user": user, "json_mode": json_mode})
        if not self.responses:
            raise AssertionError("FakeLLM: no more queued responses")
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r if isinstance(r, str) else _json.dumps(r)

    def complete_json(self, system: str, user: str, *, max_tokens: int = 1500,
                      temperature: Optional[float] = None) -> Dict[str, Any]:
        from leadgen.llm.base import parse_json_block
        return parse_json_block(self.complete(system, user, json_mode=True, max_tokens=max_tokens))
