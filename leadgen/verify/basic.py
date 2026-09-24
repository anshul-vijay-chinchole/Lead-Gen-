"""Offline email checks: the ``basic`` verifier + the shared pre-check for API verifiers.

``BasicVerifier`` never touches the network. It answers:

* ``invalid`` (``raw_status='syntax_error'``) when the address is not a
  syntactically valid email (``utils.is_valid_email``);
* ``invalid`` (``raw_status='disposable'``) when the domain (or a parent
  domain) is a known throw-away inbox provider (mailinator.com, yopmail.com,
  10minutemail.com, ...);
* ``unknown`` (``raw_status='syntax_ok'``) otherwise - deliverability can only
  be established by an SMTP-level check, which is what the API verifiers do.

It is the default verifier and the pipeline's fallback in ``--dry-run``.

This module also defines ``ApiVerifier``: the base class of every verifier
that calls a provider (MillionVerifier, ZeroBounce, NeverBounce, Hunter). It
runs the same offline pre-check first, so malformed and disposable addresses
are answered locally and never spend a provider credit, returns ``unknown``
without calling the provider in ``ctx.dry_run``, and only then resolves the API
key (lazily, so a playbook without a key can still be validated).

Config keys (all verifiers in this package)
-------------------------------------------
extra_disposable_domains
    Additional throw-away domains to treat as ``invalid`` (list of domains).
precheck_disposable
    API verifiers only; default true. With false, disposable domains are sent
    to the provider like any other address (syntax errors never are).
"""
from __future__ import annotations

from typing import Any, FrozenSet, Iterable, Optional

from ..http import HttpError, redact
from ..models import EmailStatus
from ..utils import is_valid_email, normalize_domain
from .base import VerificationResult, Verifier

# Well-known disposable / throw-away inbox providers. Deliberately conservative:
# only services whose whole purpose is temporary inboxes.
DISPOSABLE_DOMAINS: FrozenSet[str] = frozenset({
    "10minutemail.com", "10minutemail.net", "20minutemail.com", "33mail.com",
    "burnermail.io", "discard.email", "dispostable.com", "emailfake.com",
    "emailondeck.com", "fakeinbox.com", "fakemail.net", "getairmail.com",
    "getnada.com", "grr.la", "guerrillamail.biz", "guerrillamail.com",
    "guerrillamail.de", "guerrillamail.net", "guerrillamail.org",
    "guerrillamailblock.com", "harakirimail.com", "incognitomail.org",
    "inboxkitten.com", "jetable.org", "mailcatch.com", "maildrop.cc",
    "mailinator.com", "mailinator.net", "mailnesia.com", "mailpoof.com",
    "mintemail.com", "moakt.com", "mohmal.com", "mytemp.email", "nada.email",
    "sharklasers.com", "spam4.me", "spambox.us", "spamgourmet.com",
    "temp-mail.io", "temp-mail.org", "tempail.com", "tempinbox.com",
    "tempmail.com", "tempmail.net", "tempmailo.com", "tempr.email",
    "throwawaymail.com", "trashmail.com", "trashmail.de", "trashmail.net",
    "yopmail.com", "yopmail.fr", "yopmail.net",
})


def normalize_email(email: Any) -> str:
    """Trim + lowercase (``None`` -> ``''``)."""
    return str(email or "").strip().lower()


def is_disposable(email: Any, extra: Iterable[str] = ()) -> bool:
    """True when the email's domain, or one of its parent domains, is disposable."""
    domain = normalize_domain(normalize_email(email)) if "@" in normalize_email(email) else ""
    if not domain:
        return False
    blocked = set(DISPOSABLE_DOMAINS)
    blocked.update(normalize_domain(d) for d in extra or () if d)
    parts = domain.split(".")
    return any(".".join(parts[i:]) in blocked for i in range(len(parts) - 1))


def precheck(email: Any, provider: str, *, check_disposable: bool = True,
             extra_disposable: Iterable[str] = ()) -> Optional[VerificationResult]:
    """Offline verdict for addresses that need no provider call, else None.

    Returns an ``invalid`` result for syntax errors (``raw_status='syntax_error'``)
    and, when ``check_disposable``, for disposable domains (``'disposable'``).
    """
    addr = normalize_email(email)
    if not is_valid_email(addr):
        return VerificationResult(email=addr, status=EmailStatus.INVALID, raw_status="syntax_error",
                                  provider=provider, detail="not a syntactically valid email address")
    if check_disposable and is_disposable(addr, extra_disposable):
        return VerificationResult(email=addr, status=EmailStatus.INVALID, raw_status="disposable",
                                  provider=provider, detail="disposable (throw-away) email domain")
    return None


def _as_domains(value: Any) -> list:
    if not value:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [str(v).strip() for v in value if str(v).strip()]


class BasicVerifier(Verifier):
    """Syntax + disposable-domain check. Never touches the network.

    Config: ``extra_disposable_domains`` (list) - more throw-away domains.
    """

    name = "basic"
    env_key = ""
    offline = True

    def verify(self, email: str) -> VerificationResult:
        addr = normalize_email(email)
        bad = precheck(addr, self.name, check_disposable=True,
                       extra_disposable=_as_domains(self.config.get("extra_disposable_domains")))
        if bad is not None:
            return bad
        return VerificationResult(email=addr, status=EmailStatus.UNKNOWN, raw_status="syntax_ok",
                                  provider=self.name,
                                  detail="syntax ok; deliverability not checked (offline verifier)")


class ApiVerifier(Verifier):
    """Base for verifiers backed by a provider API.

    ``verify`` = offline pre-check (syntax always, disposable domains unless
    ``precheck_disposable: false``) -> dry-run short-circuit -> ``check(email)``
    (implemented by subclasses; may raise ``VerifierError`` / ``HttpError`` /
    ``MissingCredentialError`` for provider-level failures, never for a bad
    address). API keys quoted in an ``HttpError`` message (network errors
    repeat the request URL) are redacted before it propagates.
    """

    offline = False

    def verify(self, email: str) -> VerificationResult:
        addr = normalize_email(email)
        bad = precheck(addr, self.name,
                       check_disposable=bool(self.config.get("precheck_disposable", True)),
                       extra_disposable=_as_domains(self.config.get("extra_disposable_domains")))
        if bad is not None:
            return bad
        if self.ctx.dry_run:
            self.log.debug("dry-run: not calling %s for %s", self.name, addr)
            return VerificationResult(email=addr, status=EmailStatus.UNKNOWN, raw_status="dry_run",
                                      provider=self.name, detail=f"dry-run: {self.name} not called")
        try:
            return self.check(addr)
        except HttpError as e:
            # network errors quote the request URL, API key query parameter included
            # ("... Max retries exceeded with url: /api/v3/?api=KEY&email=..."): the
            # message ends up in logs, summary.json and notifications
            body = redact(e.body or "")
            if body != (e.body or ""):
                raise HttpError(e.status, e.url, body) from None
            raise

    def check(self, email: str) -> VerificationResult:  # pragma: no cover - interface
        raise NotImplementedError

    # --- helpers for subclasses ---------------------------------------------------
    def base_url(self, default: str) -> str:
        return str(self.config.get("base_url") or default).rstrip("/")

    def timeout(self, default: Optional[float] = None) -> Optional[float]:
        raw = self.config.get("timeout", default)
        if raw in (None, ""):
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{self.name}: 'timeout' must be a number of seconds, got {raw!r}") from None
        return value if value > 0 else None

    def result(self, email: str, status: str, raw_status: Any, *details: Any) -> VerificationResult:
        detail = "; ".join(str(d) for d in details if d not in (None, "", [], {}))
        return VerificationResult(email=email, status=status if status in EmailStatus.ALL else EmailStatus.UNKNOWN,
                                  raw_status="" if raw_status is None else str(raw_status),
                                  provider=self.name, detail=detail)


__all__ = ["ApiVerifier", "BasicVerifier", "DISPOSABLE_DOMAINS", "is_disposable", "normalize_email",
           "precheck"]
