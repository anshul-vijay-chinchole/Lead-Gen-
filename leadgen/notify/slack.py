"""Slack notifier (``type: slack``): post alerts to a Slack incoming webhook.

Request: ``POST <webhook_url>`` with JSON ``{"text": "*<title>*\\n<text>"}``
(Slack "mrkdwn": the title is bold). Slack answers ``200 ok``; errors come
back as 4xx with a short plain-text reason (``invalid_payload``,
``invalid_token``, ``no_service``, ``channel_not_found``,
``channel_is_archived``, ...), which is raised as ``SlackError``.

The message text is kept under Slack's ~3000-character block limit
(``max_chars``) and truncated with a marker when longer. ``&``, ``<`` and
``>`` are escaped as Slack requires, so an address like ``<jane@acme.com>``
is shown literally instead of being parsed as a Slack link token.

The webhook URL is a secret (the token is in its path), so it is never logged
and never appears in raised errors.

In ``ctx.dry_run`` ``send`` only logs what it would have posted.

Credential
----------
``webhook_url`` in the channel config, or the env var named by
``webhook_url_env``, or ``SLACK_WEBHOOK_URL``. Resolved lazily at the first
``send`` (a missing URL raises ``MissingCredentialError`` then, not at
construction, so ``leadgen validate`` can instantiate the channel).

Config keys
-----------
webhook_url      Incoming-webhook URL (``https://hooks.slack.com/services/...``).
webhook_url_env  Name of the env var holding the URL (default ``SLACK_WEBHOOK_URL``).
max_chars        Max length of the posted text (default 3000, min 200).
escape           Escape ``& < >`` in title/text (default true).
channel, username, icon_emoji, icon_url
                 Optional overrides, sent only when set (honoured by legacy
                 webhooks; Slack-app webhooks ignore them).
events           Optional list of event names this channel accepts; others
                 are silently ignored (e.g. ``[positive, referral]`` to keep
                 run summaries out of Slack).
timeout          HTTP timeout in seconds (default 10).
"""
from __future__ import annotations

import re
from typing import Any, Dict
from urllib.parse import urlparse

from ..http import HttpError
from .base import Notifier

DEFAULT_MAX_CHARS = 3000
TRUNCATION_MARKER = "\n…(truncated)"
_DANGLING_ENTITY = re.compile(r"&[a-z]{0,4}$")


class SlackError(RuntimeError):
    """Slack rejected the message (or could not be reached)."""


def escape_mrkdwn(text: str) -> str:
    """Escape the three characters Slack treats as control characters."""
    return str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _safe_host(url: str) -> str:
    """Scheme + host only: never leak the secret part of a webhook URL."""
    try:
        p = urlparse(url)
        return f"{p.scheme}://{p.hostname}" if p.hostname else "<webhook>"
    except ValueError:
        return "<webhook>"


class SlackNotifier(Notifier):
    """Post ``*title*\\ntext`` to a Slack incoming webhook (see module docstring)."""

    name = "slack"
    env_key = "SLACK_WEBHOOK_URL"

    def accepts(self, event: str) -> bool:
        events = self.config.get("events")
        return not events or event in events

    @property
    def max_chars(self) -> int:
        try:
            n = int(self.config.get("max_chars") or DEFAULT_MAX_CHARS)
        except (TypeError, ValueError):
            n = DEFAULT_MAX_CHARS
        return max(200, n)

    def build_text(self, title: str, text: str) -> str:
        esc = self.config.get("escape", True) is not False
        t = escape_mrkdwn(title) if esc else str(title or "")
        b = escape_mrkdwn(text) if esc else str(text or "")
        full = f"*{t.strip()}*" if t.strip() else ""
        if b.strip():
            full = f"{full}\n{b.rstrip()}" if full else b.rstrip()
        limit = self.max_chars
        if len(full) > limit:
            cut = full[: limit - len(TRUNCATION_MARKER)]
            cut = _DANGLING_ENTITY.sub("", cut)  # don't leave half of "&amp;"
            full = cut.rstrip() + TRUNCATION_MARKER
        return full

    def build_payload(self, event: str, title: str, text: str) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"text": self.build_text(title, text)}
        for key in ("channel", "username", "icon_emoji", "icon_url"):
            if self.config.get(key):
                payload[key] = str(self.config[key])
        return payload

    def send(self, event: str, title: str, text: str, data: Dict[str, Any]) -> None:
        if not self.accepts(event):
            return
        if self.ctx.dry_run:
            self.log.info("dry-run: not posting '%s' to Slack", title)
            return
        url = self.secret("webhook_url", "SLACK_WEBHOOK_URL").strip()
        if not url.lower().startswith(("https://", "http://")):
            raise SlackError("slack: webhook_url must be an http(s) URL "
                             "(e.g. https://hooks.slack.com/services/...)")
        payload = self.build_payload(event, title, text)
        try:
            timeout = float(self.config.get("timeout") or 10)
        except (TypeError, ValueError):
            timeout = 10.0
        try:
            resp = self.http.post(url, json=payload, timeout=timeout, raise_for_status=False)
        except HttpError as e:
            # HttpError embeds the URL (whose path is the secret) - re-raise without it.
            raise SlackError(f"slack: could not reach {_safe_host(url)} "
                             f"(HTTP {e.status}): {e.body[:200]}") from None
        if not resp.ok:
            reason = (resp.text or "").strip()[:200] or "no response body"
            raise SlackError(f"slack: webhook rejected the message (HTTP {resp.status}): {reason}")
        self.log.debug("slack: posted '%s'", title)


__all__ = ["SlackNotifier", "SlackError", "escape_mrkdwn"]
