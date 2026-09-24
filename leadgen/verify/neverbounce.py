"""NeverBounce single-email check (``type: neverbounce``).

Request: ``GET https://api.neverbounce.com/v4/single/check?key=KEY&email=...``.
Response (documented shape)::

    {"status": "success", "result": "valid",
     "flags": ["has_dns", "has_dns_mx", "smtp_connectable"],
     "suggested_correction": "", "execution_time": 285}

``result`` -> status: ``valid`` = valid, ``catchall`` = risky, ``unknown`` =
unknown, ``invalid`` / ``disposable`` = invalid (numeric result codes 0-4 are
understood too). Any top-level ``status`` other than ``success``
(``auth_failure``, ``general_failure``, ``temp_unavail``,
``throttle_triggered``, ``bad_referrer``) raises ``VerifierError`` with the
provider's ``message``.

Credential: ``NEVERBOUNCE_API_KEY`` (or config ``api_key`` / ``api_key_env``),
sent as the ``key`` query parameter.

Config keys
-----------
base_url    Default ``https://api.neverbounce.com/v4`` (e.g. ``.../v4.2``).
path        Default ``/single/check``.
timeout     Optional: seconds NeverBounce may spend (sent as ``timeout``); the
            HTTP timeout is this + 10s.
extra_disposable_domains, precheck_disposable
            See ``leadgen.verify.basic``.
"""
from __future__ import annotations

from typing import Any, Dict

from ..models import EmailStatus
from .base import VerificationResult, VerifierError
from .basic import ApiVerifier

DEFAULT_BASE_URL = "https://api.neverbounce.com/v4"
DEFAULT_PATH = "/single/check"

RESULT_MAP: Dict[str, str] = {
    "valid": EmailStatus.VALID,
    "catchall": EmailStatus.RISKY,
    "catch_all": EmailStatus.RISKY,
    "accept_all": EmailStatus.RISKY,
    "unknown": EmailStatus.UNKNOWN,
    "invalid": EmailStatus.INVALID,
    "disposable": EmailStatus.INVALID,
}
# NeverBounce's numeric result codes (older / verbose responses).
RESULT_CODES: Dict[int, str] = {0: "valid", 1: "invalid", 2: "disposable", 3: "catchall", 4: "unknown"}


class NeverBounceVerifier(ApiVerifier):
    """Verify one address with NeverBounce's ``single/check`` (see module docstring)."""

    name = "neverbounce"
    env_key = "NEVERBOUNCE_API_KEY"

    def check(self, email: str) -> VerificationResult:
        key = self.secret()
        url = self.base_url(DEFAULT_BASE_URL) + "/" + str(self.config.get("path") or DEFAULT_PATH).lstrip("/")
        params: Dict[str, Any] = {"key": key, "email": email}
        kw: Dict[str, Any] = {"params": params}
        smtp_timeout = self.timeout(None)
        if smtp_timeout:
            params["timeout"] = int(smtp_timeout)
            kw["timeout"] = smtp_timeout + 10
        data = self.http.get_json(url, **kw)
        if not isinstance(data, dict):
            raise VerifierError(f"{self.name}: unexpected response for {email}: {data!r:.200}")
        api_status = str(data.get("status") or "").strip().lower()
        if api_status != "success":
            message = data.get("message") or "no message"
            raise VerifierError(f"{self.name}: {api_status or 'missing status'}: {message}")
        result = data.get("result")
        if isinstance(result, bool):
            result = None
        if isinstance(result, (int, float)):
            result = RESULT_CODES.get(int(result), str(result))
        raw = str(result or "").strip().lower()
        if not raw:
            raise VerifierError(f"{self.name}: response for {email} has no 'result' field")
        status = RESULT_MAP.get(raw, EmailStatus.UNKNOWN)
        suggestion = str(data.get("suggested_correction") or "").strip()
        flags = data.get("flags") if isinstance(data.get("flags"), list) else []
        interesting = [f for f in flags if f in ("free_email_host", "role_account", "disposable_email",
                                                   "spamtrap_network", "bad_syntax")]
        return self.result(
            email, status, raw,
            f"flags={','.join(interesting)}" if interesting else "",
            f"did you mean {suggestion}" if suggestion else "",
        )


__all__ = ["NeverBounceVerifier", "RESULT_CODES", "RESULT_MAP"]
