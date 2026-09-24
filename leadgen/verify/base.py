"""Base class for email verifiers.

``verify(email)`` returns a ``VerificationResult`` whose ``status`` is one of
``EmailStatus.ALL``: valid | risky (catch-all/accept-all) | invalid | unknown.
Provider-specific statuses are kept in ``raw_status``. Verifiers must never
raise for a bad *email*; network/credential errors may raise.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from ..context import Adapter
from ..models import EmailStatus


class VerifierError(RuntimeError):
    """Provider-level failure (bad API key, out of credits, malformed response)."""


@dataclass
class VerificationResult:
    email: str
    status: str = EmailStatus.UNKNOWN
    raw_status: str = ""
    provider: str = ""
    detail: str = ""


class Verifier(Adapter):
    name = "verifier"

    def verify(self, email: str) -> VerificationResult:  # pragma: no cover - interface
        raise NotImplementedError

    def verify_many(self, emails: List[str]) -> List[VerificationResult]:
        return [self.verify(e) for e in emails]
