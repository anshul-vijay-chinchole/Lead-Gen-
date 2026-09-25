"""MillionVerifier single-email verification (``type: millionverifier``).

Request: ``GET https://api.millionverifier.com/api/v3/?api=KEY&email=...&timeout=10``.
Response (documented shape)::

    {"email": "jane@acme.com", "quality": "good", "result": "ok", "resultcode": 1,
     "subresult": "ok", "free": false, "role": false, "didyoumean": "",
     "credits": 9999, "executiontime": 1, "error": "", "livemode": true}

``result`` -> status: ``ok`` = valid, ``catch_all`` = risky, ``unknown`` /
``error`` = unknown, ``disposable`` / ``invalid`` = invalid (anything else =
unknown). A non-empty ``error`` field (invalid API key, insufficient credits,
...) raises ``VerifierError``; so does a response without ``result``.

Every response's ``credits`` field (credits left on the account) is passed to
the run's usage meter (``ctx.usage.note_credits``), also when the response
carries an error, so the run summary shows "N credits left".

Credential: ``MILLIONVERIFIER_API_KEY`` (or config ``api_key`` / ``api_key_env``),
sent as the ``api`` query parameter.

Config keys
-----------
timeout     Seconds MillionVerifier may spend on the SMTP check (default 10,
            the API accepts 2-60). The HTTP timeout is this + 10s.
base_url    Endpoint override (default ``https://api.millionverifier.com/api/v3/``).
extra_disposable_domains, precheck_disposable
            See ``leadgen.verify.basic``.
"""
from __future__ import annotations

from typing import Any, Dict

from ..models import EmailStatus
from .base import VerificationResult, VerifierError
from .basic import ApiVerifier

DEFAULT_URL = "https://api.millionverifier.com/api/v3/"

RESULT_MAP: Dict[str, str] = {
    "ok": EmailStatus.VALID,
    "catch_all": EmailStatus.RISKY,
    "catchall": EmailStatus.RISKY,
    "unknown": EmailStatus.UNKNOWN,
    "error": EmailStatus.UNKNOWN,
    "disposable": EmailStatus.INVALID,
    "invalid": EmailStatus.INVALID,
}


class MillionVerifier(ApiVerifier):
    """Verify one address with MillionVerifier's real-time API (see module docstring)."""

    name = "millionverifier"
    env_key = "MILLIONVERIFIER_API_KEY"

    def check(self, email: str) -> VerificationResult:
        key = self.secret()
        smtp_timeout = self.timeout(10) or 10
        url = str(self.config.get("base_url") or DEFAULT_URL)
        params = {"api": key, "email": email, "timeout": int(smtp_timeout)}
        data = self.http.get_json(url, params=params, timeout=smtp_timeout + 10)
        if not isinstance(data, dict):
            raise VerifierError(f"{self.name}: unexpected response for {email}: {data!r:.200}")
        self._note_credits(data.get("credits"))
        error = data.get("error")
        if error not in (None, "", False):
            raise VerifierError(f"{self.name}: {error}")
        raw = str(data.get("result") or "").strip().lower()
        if not raw:
            raise VerifierError(f"{self.name}: response for {email} has no 'result' field")
        status = RESULT_MAP.get(raw, EmailStatus.UNKNOWN)
        sub = str(data.get("subresult") or "").strip()
        did_you_mean = str(data.get("didyoumean") or "").strip()
        return self.result(
            email, status, raw,
            f"subresult={sub}" if sub and sub.lower() != raw else "",
            f"quality={data.get('quality')}" if data.get("quality") else "",
            "role address" if data.get("role") is True else "",
            "free email provider" if data.get("free") is True else "",
            f"did you mean {did_you_mean}" if did_you_mean else "",
        )

    def _note_credits(self, credits: Any) -> None:
        """Pass the account's remaining credits (when the response has them) to the usage meter."""
        meter = getattr(self.ctx, "usage", None)
        if meter is None or credits is None or credits == "" or isinstance(credits, bool):
            return
        meter.note_credits(self.adapter_kind or "verifier", self.type_name or self.name, credits)


__all__ = ["MillionVerifier", "RESULT_MAP"]
