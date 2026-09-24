"""ZeroBounce single-email validation (``type: zerobounce``).

Request: ``GET https://api.zerobounce.net/v2/validate?api_key=KEY&email=...&ip_address=``.
Response (documented shape)::

    {"address": "jane@acme.com", "status": "valid", "sub_status": "",
     "free_email": false, "did_you_mean": null, "account": "jane",
     "domain": "acme.com", "mx_found": "true", "mx_record": "mx.acme.com",
     "smtp_provider": "google", "processed_at": "2026-09-24 10:00:00.000"}

``status`` -> status: ``valid`` = valid, ``catch-all`` = risky, ``unknown`` =
unknown, ``invalid`` / ``spamtrap`` / ``abuse`` / ``do_not_mail`` = invalid
(anything else = unknown). ``sub_status`` is kept in ``detail``. A response
carrying ``error`` (e.g. "Invalid API key or your account ran out of credits")
or no ``status`` raises ``VerifierError``.

Credential: ``ZEROBOUNCE_API_KEY`` (or config ``api_key`` / ``api_key_env``),
sent as the ``api_key`` query parameter.

Config keys
-----------
base_url    Default ``https://api.zerobounce.net/v2`` (use
            ``https://api-us.zerobounce.net/v2`` / ``https://api-eu.zerobounce.net/v2``
            for a regional endpoint).
path        Default ``/validate``.
timeout     Optional: seconds ZeroBounce may spend (sent as ``timeout``, 3-60);
            the HTTP timeout is this + 10s.
ip_address  Optional value for the ``ip_address`` parameter (default empty).
extra_disposable_domains, precheck_disposable
            See ``leadgen.verify.basic``.
"""
from __future__ import annotations

from typing import Any, Dict

from ..models import EmailStatus
from .base import VerificationResult, VerifierError
from .basic import ApiVerifier

DEFAULT_BASE_URL = "https://api.zerobounce.net/v2"
DEFAULT_PATH = "/validate"

STATUS_MAP: Dict[str, str] = {
    "valid": EmailStatus.VALID,
    "catch-all": EmailStatus.RISKY,
    "catch_all": EmailStatus.RISKY,
    "catchall": EmailStatus.RISKY,
    "unknown": EmailStatus.UNKNOWN,
    "invalid": EmailStatus.INVALID,
    "spamtrap": EmailStatus.INVALID,
    "abuse": EmailStatus.INVALID,
    "do_not_mail": EmailStatus.INVALID,
}


class ZeroBounceVerifier(ApiVerifier):
    """Verify one address with ZeroBounce's ``/v2/validate`` (see module docstring)."""

    name = "zerobounce"
    env_key = "ZEROBOUNCE_API_KEY"

    def check(self, email: str) -> VerificationResult:
        key = self.secret()
        url = self.base_url(DEFAULT_BASE_URL) + "/" + str(self.config.get("path") or DEFAULT_PATH).lstrip("/")
        params: Dict[str, Any] = {"api_key": key, "email": email,
                                  "ip_address": str(self.config.get("ip_address") or "")}
        kw: Dict[str, Any] = {"params": params}
        smtp_timeout = self.timeout(None)
        if smtp_timeout:
            params["timeout"] = int(smtp_timeout)
            kw["timeout"] = smtp_timeout + 10
        data = self.http.get_json(url, **kw)
        if not isinstance(data, dict):
            raise VerifierError(f"{self.name}: unexpected response for {email}: {data!r:.200}")
        error = data.get("error") or data.get("Error")
        if error:
            raise VerifierError(f"{self.name}: {error}")
        raw = str(data.get("status") or "").strip().lower()
        if not raw:
            raise VerifierError(f"{self.name}: response for {email} has no 'status' field")
        status = STATUS_MAP.get(raw, EmailStatus.UNKNOWN)
        sub = str(data.get("sub_status") or "").strip()
        suggestion = str(data.get("did_you_mean") or "").strip()
        return self.result(
            email, status, raw,
            f"sub_status={sub}" if sub else "",
            "free email provider" if data.get("free_email") in (True, "true", "True") else "",
            f"did you mean {suggestion}" if suggestion else "",
        )


__all__ = ["STATUS_MAP", "ZeroBounceVerifier"]
