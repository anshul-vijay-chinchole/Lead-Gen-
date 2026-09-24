"""Hunter.io email verification (``type: hunter``).

Request: ``GET https://api.hunter.io/v2/email-verifier?email=...&api_key=KEY``.
Response (documented shape)::

    {"data": {"status": "valid", "result": "deliverable", "score": 91,
              "email": "jane@acme.com", "regexp": true, "gibberish": false,
              "disposable": false, "webmail": false, "mx_records": true,
              "smtp_server": true, "smtp_check": true, "accept_all": false,
              "block": false, "sources": []},
     "meta": {"params": {"email": "jane@acme.com"}}}

``data.status`` -> status: ``valid`` = valid, ``accept_all`` = risky,
``webmail`` = valid when ``result`` is ``deliverable`` else unknown,
``invalid`` / ``disposable`` = invalid, anything else (``unknown``) = unknown.
Without ``status`` the legacy ``result`` is used (deliverable = valid,
undeliverable = invalid, risky = risky).

HTTP statuses Hunter uses for "no verdict yet" are answered, not raised:
``202`` (verification still running - retry later) and ``222`` (the remote
SMTP server answered unexpectedly) -> unknown; ``451`` (Hunter will not process
this address) -> unknown. Other non-2xx responses raise ``HttpError``.

Credential: ``HUNTER_API_KEY`` (or config ``api_key`` / ``api_key_env``), sent
as the ``api_key`` query parameter.

Config keys
-----------
base_url    Default ``https://api.hunter.io/v2``.
path        Default ``/email-verifier``.
extra_disposable_domains, precheck_disposable
            See ``leadgen.verify.basic``.
"""
from __future__ import annotations

from typing import Dict

from ..http import HttpError
from ..models import EmailStatus
from ..utils import get_path
from .base import VerificationResult, VerifierError
from .basic import ApiVerifier

DEFAULT_BASE_URL = "https://api.hunter.io/v2"
DEFAULT_PATH = "/email-verifier"

STATUS_MAP: Dict[str, str] = {
    "valid": EmailStatus.VALID,
    "accept_all": EmailStatus.RISKY,
    "invalid": EmailStatus.INVALID,
    "disposable": EmailStatus.INVALID,
    "unknown": EmailStatus.UNKNOWN,
}
LEGACY_RESULT_MAP: Dict[str, str] = {
    "deliverable": EmailStatus.VALID,
    "undeliverable": EmailStatus.INVALID,
    "risky": EmailStatus.RISKY,
}
# HTTP status -> (raw_status, detail) for answers that carry no verdict.
NO_VERDICT: Dict[int, tuple] = {
    202: ("pending", "verification still running at Hunter; retry later"),
    222: ("smtp_error", "remote SMTP server answered unexpectedly; retry later"),
    451: ("unavailable", "Hunter declined to process this address (HTTP 451)"),
}


class HunterVerifier(ApiVerifier):
    """Verify one address with Hunter's ``email-verifier`` (see module docstring)."""

    name = "hunter"
    env_key = "HUNTER_API_KEY"

    def check(self, email: str) -> VerificationResult:
        key = self.secret()
        url = self.base_url(DEFAULT_BASE_URL) + "/" + str(self.config.get("path") or DEFAULT_PATH).lstrip("/")
        resp = self.http.get(url, params={"email": email, "api_key": key}, raise_for_status=False)
        if resp.status in NO_VERDICT:
            raw, detail = NO_VERDICT[resp.status]
            return self.result(email, EmailStatus.UNKNOWN, raw, detail)
        if not resp.ok:
            raise HttpError(resp.status, url, resp.text)
        try:
            body = resp.json()
        except ValueError as e:
            raise VerifierError(f"{self.name}: response for {email} is not JSON: {e}") from e
        data = get_path(body, "data")
        if not isinstance(data, dict):
            errors = get_path(body, "errors")
            if errors:
                raise VerifierError(f"{self.name}: {errors}")
            raise VerifierError(f"{self.name}: response for {email} has no 'data' object")
        raw = str(data.get("status") or "").strip().lower()
        result = str(data.get("result") or "").strip().lower()
        if raw == "webmail":
            status = EmailStatus.VALID if result == "deliverable" else EmailStatus.UNKNOWN
        elif raw:
            status = STATUS_MAP.get(raw, EmailStatus.UNKNOWN)
        else:
            raw = result
            status = LEGACY_RESULT_MAP.get(result, EmailStatus.UNKNOWN)
        score = data.get("score")
        return self.result(
            email, status, raw,
            f"result={result}" if result and result != raw else "",
            f"score={score}" if score not in (None, "") else "",
        )


__all__ = ["HunterVerifier", "LEGACY_RESULT_MAP", "NO_VERDICT", "STATUS_MAP"]
