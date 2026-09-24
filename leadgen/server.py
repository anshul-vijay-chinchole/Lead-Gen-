"""Webhook receiver for reply events (``leadgen serve``).

A tiny HTTP server built on the standard library (``http.server``) - no web
framework, no extra dependency. Point your sending tool's "reply received"
webhook at it and every reply is classified and acted on the moment it
arrives, exactly like ``leadgen replies --file``:

    webhook JSON -> replies.parse_webhook_payload -> replies.handle_reply
                 -> stage change / suppression / follow-up / alert -> JSON answer

Endpoints
---------
``GET  /health``
    ``{"ok": true, "playbook": "<name>"}`` - for uptime checks. Never needs the token.
``POST /webhook`` ``/webhook/reply`` ``/webhook/instantly`` ``/webhook/smartlead``
    All four accept the same payloads (Instantly, Smartlead or a generic
    ``{from_email, body, subject, received_at}`` shape - the parser detects the
    shape itself; the separate paths only make provider set-up read better).
    Answers ``200 {"ok": true, "category": ..., "action": ..., "lead_id": ...}``
    for a reply, ``200 {"ok": true, "ignored": true}`` for events that are not
    replies (sent, opened, clicked, ...). A JSON *array* of events is accepted
    too; the answer is then ``{"ok": true, "results": [...]}``.

Errors: ``400`` body is not JSON; ``401`` wrong or missing token (only when a
token is configured); ``404`` unknown path; ``405`` wrong method; ``411`` no
``Content-Length``; ``413`` body larger than ``MAX_BODY_BYTES`` (1 MB); ``500``
the reply could not be processed (logged; providers retry, and retries are
safe because ``handle_reply`` skips replies it has already stored).

Security
--------
With a token (``make_server(..., token=...)``, ``leadgen serve --token`` or
``$LEADGEN_WEBHOOK_TOKEN``) every webhook request must carry it, either in the
``X-Leadgen-Token`` header or as ``?token=`` in the URL (most sending tools can
only set the URL). Tokens are compared in constant time. The server binds to
``127.0.0.1`` by default; to receive webhooks from the internet put it behind a
tunnel or reverse proxy with HTTPS, and always set a token then.

The server is single-threaded (one request at a time), which keeps SQLite
access simple and is plenty for reply volumes. Every response closes its
connection and a client has ``WebhookHandler.timeout`` seconds to send its
request, so one slow or idle client cannot block the others. Request logging is quiet: it
goes to ``ctx.log`` at debug level (``leadgen -vv serve`` shows it).
"""
from __future__ import annotations

import hmac
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional, Tuple, Type
from urllib.parse import parse_qs, urlsplit

from .replies import handle_reply, parse_webhook_payload

#: Largest request body accepted (bytes).
MAX_BODY_BYTES = 1024 * 1024
#: Paths that accept reply webhooks.
WEBHOOK_PATHS = frozenset({"/webhook", "/webhook/reply", "/webhook/instantly", "/webhook/smartlead"})
HEALTH_PATH = "/health"
TOKEN_HEADER = "X-Leadgen-Token"
#: Max events processed from one JSON array body.
MAX_EVENTS_PER_REQUEST = 100
#: An oversized body up to this size is read and discarded before answering 413, so
#: the client sees the answer instead of a connection reset; beyond it we just close.
MAX_DRAIN_BYTES = 8 * MAX_BODY_BYTES


class WebhookServer(HTTPServer):
    """``HTTPServer`` that carries the run context and the optional token."""

    allow_reuse_address = True

    def __init__(self, address: Tuple[str, int], handler: Type[BaseHTTPRequestHandler], ctx: Any,
                 token: Optional[str] = None):
        self.ctx = ctx
        self.token = token or None
        super().__init__(address, handler)

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}"


class WebhookHandler(BaseHTTPRequestHandler):
    """Routes ``/health`` and the ``/webhook*`` endpoints (see module docstring)."""

    server: WebhookServer
    server_version = "leadgen-webhook/1"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    # Seconds a client may take to send its request. The server is single-threaded, so
    # every response also closes the connection: an idle keep-alive client must never
    # block the next webhook.
    timeout = 30

    # --- plumbing ----------------------------------------------------------------------
    @property
    def ctx(self) -> Any:
        return self.server.ctx

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        self.ctx.log.debug("webhook %s - " + format, self.address_string(), *args)

    def _send_json(self, status: int, payload: Dict[str, Any],
                   headers: Optional[Dict[str, str]] = None) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.close_connection = True
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, status: int, message: str, headers: Optional[Dict[str, str]] = None) -> None:
        self._send_json(status, {"ok": False, "error": message}, headers)

    def _split(self) -> Tuple[str, Dict[str, List[str]]]:
        parts = urlsplit(self.path)
        path = parts.path.rstrip("/") or "/"
        return path, parse_qs(parts.query)

    def _authorized(self, query: Dict[str, List[str]]) -> bool:
        expected = self.server.token
        if not expected:
            return True
        given = self.headers.get(TOKEN_HEADER) or (query.get("token") or [""])[0]
        return bool(given) and hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))

    def _read_body(self) -> Optional[bytes]:
        """Read the request body; answers 411/413 itself and returns None then."""
        length_raw = self.headers.get("Content-Length")
        if length_raw is None:
            if self.headers.get("Transfer-Encoding"):
                self.close_connection = True
                self._error(411, "Content-Length required")
                return None
            return b""
        try:
            length = int(length_raw)
        except ValueError:
            self.close_connection = True
            self._error(400, "invalid Content-Length")
            return None
        if length < 0:
            self.close_connection = True
            self._error(400, "invalid Content-Length")
            return None
        if length > MAX_BODY_BYTES:
            self.close_connection = True
            if length <= MAX_DRAIN_BYTES:
                self._discard(length)
            self._error(413, f"body too large (max {MAX_BODY_BYTES} bytes)")
            return None
        return self.rfile.read(length) if length else b""

    def _discard(self, length: int) -> None:
        remaining = length
        while remaining > 0:
            chunk = self.rfile.read(min(65536, remaining))
            if not chunk:
                break
            remaining -= len(chunk)

    # --- routes --------------------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        path, _ = self._split()
        if path == HEALTH_PATH:
            self._send_json(200, {"ok": True, "playbook": self.ctx.playbook.name})
        elif path in WEBHOOK_PATHS:
            self._error(405, "use POST for webhooks", {"Allow": "POST"})
        else:
            self._error(404, "not found")

    do_HEAD = do_GET

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        path, query = self._split()
        if path not in WEBHOOK_PATHS:
            self.close_connection = True  # the body is not read: do not reuse the connection
            if path == HEALTH_PATH:
                self._error(405, "use GET for /health", {"Allow": "GET"})
            else:
                self._error(404, "not found")
            return
        if not self._authorized(query):
            self.ctx.log.warning("webhook: rejected a request with a missing/wrong token from %s",
                                 self.address_string())
            self.close_connection = True
            self._error(401, "missing or invalid token")
            return
        raw = self._read_body()
        if raw is None:
            return
        try:
            payload = json.loads(raw.decode("utf-8")) if raw.strip() else None
        except (UnicodeDecodeError, ValueError):
            self._error(400, "body is not valid JSON")
            return
        if payload is None:
            self._error(400, "empty body - expected a JSON object")
            return
        try:
            if isinstance(payload, list):
                results = [self._process(item) for item in payload[:MAX_EVENTS_PER_REQUEST]]
                self._send_json(200, {"ok": True, "results": results})
            else:
                self._send_json(200, self._process(payload))
        except Exception as e:  # noqa: BLE001 - never kill the server on one bad event
            self.ctx.log.exception("webhook: could not process event on %s: %s", path, e)
            self._error(500, "could not process the event")

    def _process(self, payload: Any) -> Dict[str, Any]:
        reply = parse_webhook_payload(payload)
        if reply is None:
            return {"ok": True, "ignored": True}
        reply = handle_reply(reply, self.ctx)
        self.ctx.log.info("webhook: reply from %s -> %s (%s)", reply.from_email, reply.category,
                          reply.action)
        return {"ok": True, "category": reply.category, "action": reply.action,
                "lead_id": reply.lead_id or None, "from_email": reply.from_email}


def make_server(ctx: Any, host: str = "127.0.0.1", port: int = 8787,
                token: Optional[str] = None) -> WebhookServer:
    """Create (and bind) the webhook server. ``port=0`` picks a free port
    (read it back from ``server.server_address``). Call ``serve_forever()`` to run it."""
    if ctx.store is None:
        raise RuntimeError("make_server needs ctx.store (a leadgen.store.Store)")
    return WebhookServer((host, int(port)), WebhookHandler, ctx, token=token)


__all__ = ["HEALTH_PATH", "MAX_BODY_BYTES", "TOKEN_HEADER", "WEBHOOK_PATHS", "WebhookHandler",
           "WebhookServer", "make_server"]
