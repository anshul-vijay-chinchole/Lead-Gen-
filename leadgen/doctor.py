"""``leadgen doctor``: test every API key a playbook uses - one FREE call per key.

``run_doctor(ctx)`` builds every enabled source / finder / verifier / exporter /
notifier of the playbook in ``ctx`` (plus the writer's AI model when
``writer.provider`` is set) and checks each credential with one minimal call
to a free endpoint: an account, credit-balance, health or model-list endpoint.
It never makes a paid lookup (no search, no enrichment, no verification, no
AI completion) and never raises: every problem becomes a ``DoctorResult``.

Statuses
--------
``ok``           the provider accepted the key (``quota`` shows the credits /
                 plan it reported, when it reports any).
``failed``       the key was rejected (HTTP 401 / 403), the provider answered
                 with another error, the host could not be reached, or the
                 adapter's config is broken. ``detail`` says which.
``missing_key``  no key configured; ``detail`` names the environment variable.
``skipped``      nothing to test: the adapter needs no key (csv, pattern,
                 greenhouse ...), a test would post a real message (Slack /
                 webhooks), the adapter is not used in this mode, or it is a
                 dry run (keys are still checked for presence).

What is called (default endpoints; ``doctor_url`` overrides one)
-----------------------------------------------------------------
apollo (source / finder)   GET {base_url}/v1/auth/health, header ``x-api-key``
                           -> ``{"is_logged_in": true}``
hunter (finder / verifier) GET {base_url}/account?api_key=  -> searches /
                           verifications / credits used vs available
millionverifier            GET {base_url}/credits?api=      -> ``{"credits": N}``
zerobounce                 GET {base_url}/getcredits?api_key= -> ``{"Credits": "N"}``
                           ("-1" means the key is invalid)
neverbounce                GET {base_url}/account/info?key= -> ``credits_info``
theirstack                 GET https://<host>/v0/billing/credit-balance (Bearer)
                           -> ``api_credits`` / ``used_api_credits`` / ``ui_credits``.
                           UNCERTAIN: TheirStack documents this billing endpoint
                           less formally than its job search; if it moves, set
                           ``doctor_url`` on the source.
adzuna                     GET {base_url}/{first country}/search/1?results_per_page=1
                           (the free search API; there is no account endpoint)
apify / linkedin_jobs      GET {base_url}/users/me?token=  -> username + plan
anthropic                  GET {api root}/v1/models/{model} with ``x-api-key`` +
                           ``anthropic-version``: confirms the key AND that
                           ``writer.model`` exists (404 = unknown model).
openai / openai_compatible GET {base_url}/models (Bearer) - also warns when
                           ``writer.model`` is not in the returned list.
instantly                  GET {base_url}/api/v2/campaigns?limit=1 (Bearer)
smartlead                  GET {base_url}/campaigns?api_key= (also checks that
                           ``campaign_id`` is one of the account's campaigns)
gsheets                    no network: the service-account key (file or JSON)
                           must be JSON with ``client_email``; gspread installed.
slack / webhook            skipped - a test would post a real message (the URL
                           must still be set, else ``missing_key``).
csv / json / pattern / basic / console / greenhouse / lever / ashby / upload CSVs
                           skipped - "no key needed" (input files must exist).

Config keys read (per adapter entry, besides each adapter's own ``api_key`` /
``api_key_env`` / ``base_url`` / ``label`` / ``enabled``)
-------------------------------------------------------------------------
doctor_url      Full URL of the check endpoint (overrides the default above).
                For ``anthropic`` a ``{model}`` placeholder is replaced by the
                model id. The writer's AI model reads it from ``writer.llm``.
countries       adzuna: the first country is used for the test search.
campaign_id     smartlead: checked against the campaign list.
service_account_file / service_account_json   gsheets credentials.

Metering and secrets
--------------------
Requests go straight through ``ctx.http`` - NOT the metered ``adapter.http`` -
on purpose: these are free endpoints, so they must not count as paid lookups
(``ctx.usage.paid_lookups`` stays 0) or be refused by a ``--budget`` cap. The
adapter is still built normally and its key resolved with ``adapter.secret()``
(``api_key`` > ``api_key_env`` > default variable), exactly as a run would.
Keys never appear in results: every message goes through ``http.redact`` and
each key the check used is masked literally (some providers echo it back).
Two adapters that send the identical request (e.g. an Apollo source and an
Apollo finder with the same key) are tested once. In a dry run
(``ctx.dry_run``) nothing is contacted; keys are only checked for presence.
Adapters flagged by ``registry.risk_note`` (scrapers) get the note in ``detail``.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import quote, urlsplit

from . import registry
from .context import Adapter, MissingCredentialError
from .http import HttpError, redact, safe_url
from .modes import is_outbound
from .utils import get_path

OK = "ok"
FAILED = "failed"
MISSING_KEY = "missing_key"
SKIPPED = "skipped"
STATUSES = (OK, FAILED, MISSING_KEY, SKIPPED)

TIMEOUT = 20.0  # seconds per check request

APOLLO_BASE = "https://api.apollo.io"
HUNTER_BASE = "https://api.hunter.io/v2"
MILLIONVERIFIER_BASE = "https://api.millionverifier.com/api/v3"
ZEROBOUNCE_BASE = "https://api.zerobounce.net/v2"
NEVERBOUNCE_BASE = "https://api.neverbounce.com/v4"
THEIRSTACK_BASE = "https://api.theirstack.com/v1"
THEIRSTACK_CREDITS_PATH = "/v0/billing/credit-balance"
ADZUNA_BASE = "https://api.adzuna.com/v1/api/jobs"
APIFY_BASE = "https://api.apify.com/v2"
INSTANTLY_BASE = "https://api.instantly.ai"
SMARTLEAD_BASE = "https://server.smartlead.ai/api/v1"
ANTHROPIC_VERSION = "2023-06-01"

GOOGLE_FILE_ENV = "GOOGLE_APPLICATION_CREDENTIALS"
GOOGLE_JSON_ENV = "GOOGLE_SERVICE_ACCOUNT_JSON"

_MARKS = {OK: "[ok]   ", FAILED: "[FAIL] ", MISSING_KEY: "[MISS] ", SKIPPED: "[skip] "}


@dataclass
class DoctorResult:
    """The outcome of one check. ``status``: ok | failed | missing_key | skipped."""

    kind: str       # source | finder | verifier | llm | exporter | notifier | delivery
    type: str       # the adapter type, e.g. apollo
    label: str      # the entry's label (default: its type); "writer (<model>)" for the AI model
    status: str
    detail: str
    quota: str = ""  # credits / plan the provider reported ("" = not reported)

    @property
    def problem(self) -> bool:
        """True when this needs fixing (failed or missing key)."""
        return self.status in (FAILED, MISSING_KEY)

    def line(self) -> str:
        """One printable line, e.g. ``[ok]   verifier millionverifier: key accepted (9,950 credits left)``."""
        mark = _MARKS.get(self.status, "[?]    ")
        name =f"{self.kind} {self.type}" + (f" '{self.label}'" if self.label and self.label != self.type else "")
        quota = f" ({self.quota})" if self.quota else ""
        return f"{mark}{name}: {self.detail}{quota}"

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


def format_results(results: Sequence[DoctorResult]) -> List[str]:
    """Printable lines for ``results`` plus a one-line summary."""
    lines = [r.line() for r in results]
    counts = {s: sum(1 for r in results if r.status == s) for s in STATUSES}
    lines.append(f"{counts[OK]} ok, {counts[FAILED]} failed, {counts[MISSING_KEY]} missing key, "
                 f"{counts[SKIPPED]} skipped")
    return lines


# --- errors raised inside checks (turned into results by Doctor.check) -----------------------------

class CheckFailed(Exception):
    """The check found a problem (status ``failed``); the message is the detail."""


class CheckSkipped(Exception):
    """Nothing was tested (status ``skipped``); the message is the detail."""


class KeyMissing(Exception):
    """A credential is not configured (status ``missing_key``); the message names the variable."""


# --- small helpers ----------------------------------------------------------------------------------

def result(adapter: Any, status: str, detail: str, quota: str = "") -> DoctorResult:
    """A ``DoctorResult`` for ``adapter`` (kind / type / label taken from the adapter)."""
    type_ = str(getattr(adapter, "type_name", "") or getattr(adapter, "name", "") or "?")
    kind = str(getattr(adapter, "adapter_kind", "") or "adapter")
    config = getattr(adapter, "config", None) or {}
    label = str(config.get("label") or type_)
    return DoctorResult(kind=kind, type=type_, label=label, status=status, detail=detail, quota=quota)


def number(value: Any) -> Optional[float]:
    """A number from a JSON value (int, float or numeric string like "1,250"), else None."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def fmt(value: float) -> str:
    """1250.0 -> '1,250'; 12.5 -> '12.5'."""
    return f"{int(value):,}" if float(value).is_integer() else f"{value:,.2f}".rstrip("0").rstrip(".")


def base_url(adapter: Any, default: str) -> str:
    """The adapter's ``base_url`` config (or ``default``), without a trailing '/'."""
    return str(adapter.config.get("base_url") or default).strip().rstrip("/")


def doctor_url(adapter: Any, default: str) -> str:
    """The adapter's ``doctor_url`` config (full check URL), else ``default``."""
    return str(adapter.config.get("doctor_url") or "").strip() or default


def provider_message(text: str) -> str:
    """The provider's error message from a response body ('' when there is none)."""
    try:
        data = json.loads(text) if text else None
    except ValueError:
        data = None
    candidates: List[Any] = []
    if isinstance(data, dict):
        err = data.get("error")
        errors = data.get("errors")
        first = errors[0] if isinstance(errors, list) and errors else None
        candidates = [err.get("message") if isinstance(err, dict) else None,
                      data.get("message"), data.get("detail"), data.get("display"),
                      (first.get("details") or first.get("message")) if isinstance(first, dict) else first,
                      err, data.get("exception")]
    elif data is None and text and not text.lstrip().startswith("<"):
        candidates = [text]  # a plain-text body (HTML error pages are skipped)
    for c in candidates:
        if isinstance(c, str) and c.strip():
            msg = " ".join(c.split())
            return msg if len(msg) <= 160 else msg[:157] + "..."
    return ""


def _host(url: str) -> str:
    try:
        return urlsplit(url).hostname or "the provider"
    except ValueError:
        return "the provider"


def _gspread_installed() -> bool:
    if "gspread" in sys.modules:  # tests inject a fake module; None = import blocked
        return sys.modules["gspread"] is not None
    try:
        return importlib.util.find_spec("gspread") is not None
    except (ImportError, ValueError):
        return False


CheckFn = Callable[["Doctor", Any], DoctorResult]


class Doctor:
    """One doctor run over ``ctx.playbook``. ``run()`` returns the results in playbook order.

    Check functions get this object plus the built adapter and use ``key`` /
    ``keys`` (resolve credentials through ``adapter.secret``) and ``get``
    (one un-metered GET that turns HTTP problems into ``CheckFailed``).
    """

    def __init__(self, ctx: Any):
        self.ctx = ctx
        self._answers: Dict[str, Tuple[int, str]] = {}  # request fingerprint -> (status, body)
        self._secrets: List[str] = []   # keys used by the current check (masked in its messages)
        self._reused = False            # the current check reused an earlier identical request

    # --- the run ------------------------------------------------------------------------
    def run(self, google_sheet: Optional[Dict[str, Any]] = None) -> List[DoctorResult]:
        pb = self.ctx.playbook
        results: List[Optional[DoctorResult]] = []
        results += [self.entry("source", cfg) for cfg in pb.sources or []]
        results += [self.entry("finder", cfg) for cfg in pb.enrichment.get("finders") or []]
        verifier = pb.enrichment.get("verifier")
        if verifier:
            results.append(self.entry("verifier", verifier))
        results.append(self.writer_llm())
        results += [self.entry("exporter", cfg) for cfg in pb.outbound.get("exporters") or []]
        results += [self.entry("notifier", cfg) for cfg in pb.notify.get("channels") or []]
        if google_sheet:
            results.append(self.google_sheet(google_sheet))
        return [r for r in results if r is not None]

    def entry(self, kind: str, cfg: Any) -> Optional[DoctorResult]:
        """Build one playbook entry and check it (None when it is disabled)."""
        if not isinstance(cfg, dict) or not cfg.get("type"):
            # the entry itself is never echoed: it may hold an api_key
            got = "a mapping without 'type'" if isinstance(cfg, dict) else type(cfg).__name__
            return DoctorResult(kind, "?", "?", FAILED, f"entry must be a mapping with a 'type' (got {got})")
        if cfg.get("enabled") is False:
            return None
        type_ = str(cfg["type"])
        label = str(cfg.get("label") or type_)
        try:
            adapter = registry.create(kind, cfg, self.ctx)
        except registry.UnknownAdapterError as e:
            return DoctorResult(kind, type_, label, FAILED, str(e.args[0] if e.args else e))
        except Exception as e:  # noqa: BLE001 - import error / bad config in __init__
            return DoctorResult(kind, type_, label, FAILED, f"cannot be created: {redact(str(e))}")
        return self.check(adapter)

    def writer_llm(self) -> Optional[DoctorResult]:
        """The AI model of ``writer.provider`` (used for AI opening lines / emails), if set."""
        writer = self.ctx.playbook.writer
        provider = str(writer.get("provider") or "")
        if not provider:
            return None
        from .llm import build_llm  # local import: optional feature
        try:
            adapter = build_llm(self.ctx)
        except registry.UnknownAdapterError as e:
            return DoctorResult("llm", provider, "writer", FAILED, str(e.args[0] if e.args else e))
        except Exception as e:  # noqa: BLE001
            return DoctorResult("llm", provider, "writer", FAILED, f"cannot be created: {redact(str(e))}")
        model = str(getattr(adapter, "model", "") or "")
        return self.check(adapter, label=f"writer ({model})" if model else "writer")

    def google_sheet(self, cfg: Dict[str, Any]) -> Optional[DoctorResult]:
        """A client's Google Sheets push (``delivery.google_sheet`` of a client file), if configured."""
        if not isinstance(cfg, dict) or not (cfg.get("spreadsheet_id") or cfg.get("spreadsheet_url")):
            return None
        creds = _SheetCredentials(dict(cfg), self.ctx)
        creds.adapter_kind, creds.type_name = "delivery", "google_sheet"
        return self.check(creds, label="Google Sheet push")

    def check(self, adapter: Any, label: Optional[str] = None) -> DoctorResult:
        """Run the check for one built adapter. Never raises."""
        kind = str(getattr(adapter, "adapter_kind", "") or "adapter")
        type_ = str(getattr(adapter, "type_name", "") or getattr(adapter, "name", ""))
        self._secrets, self._reused = [], False
        if kind == "exporter" and getattr(adapter, "scope", "all") == "outbound" and not is_outbound(self.ctx):
            res = result(adapter, SKIPPED, "hands leads to a sending tool - not used in delivery mode")
        else:
            fn = CHECKS.get((kind, type_)) or (check_no_key if getattr(adapter, "offline", False)
                                               else check_unknown)
            res = self._run_check(fn, adapter)
        if self._reused and res.status in (OK, FAILED):
            res.detail += " (same key and endpoint as a check above - tested once)"
        note = registry.risk_note(kind, type_, getattr(adapter, "config", None) or {})
        if note:
            res.detail += f" - {note}"
        if label:
            res.label = label
        res.detail, res.quota = self.scrub(res.detail), self.scrub(res.quota)
        return res

    def _run_check(self, fn: CheckFn, adapter: Any) -> DoctorResult:
        try:
            return fn(self, adapter)
        except KeyMissing as e:
            return result(adapter, MISSING_KEY, str(e))
        except MissingCredentialError as e:
            return result(adapter, MISSING_KEY, str(e))
        except CheckSkipped as e:
            return result(adapter, SKIPPED, str(e))
        except CheckFailed as e:
            return result(adapter, FAILED, str(e))
        except ValueError as e:  # the adapter's own config validation (e.g. adzuna countries)
            return result(adapter, FAILED, f"config problem: {e}")
        except Exception as e:  # noqa: BLE001 - LLMConfigError, a plugin's bug, ...
            text = str(e) or type(e).__name__
            if getattr(e, "permanent", False):
                return result(adapter, FAILED, f"config problem: {text}")
            return result(adapter, FAILED, f"check failed unexpectedly ({type(e).__name__}: {text})")

    # --- helpers for check functions ------------------------------------------------------
    def key(self, adapter: Any, config_key: str = "api_key", env_key: Optional[str] = None) -> str:
        """Resolve one credential with ``adapter.secret``; raises ``KeyMissing`` naming the variable."""
        return self.keys(adapter, [(config_key, env_key)])[0]

    def keys(self, adapter: Any, pairs: Iterable[Tuple[str, Optional[str]]]) -> List[str]:
        """Resolve several credentials; ``KeyMissing`` lists every missing one at once."""
        values: List[str] = []
        missing: List[Tuple[str, str]] = []
        for config_key, env_key in pairs:
            try:
                values.append(adapter.secret(config_key, env_key))
            except MissingCredentialError:
                env_name = str(adapter.config.get(f"{config_key}_env") or env_key or adapter.env_key or "")
                missing.append((config_key, env_name))
        if missing:
            envs = " and ".join(f"${env}" for _, env in missing if env)
            cfg_keys = " / ".join(f"'{k}'" for k, _ in missing)
            where = f"set {envs} (or {cfg_keys} in the playbook)" if envs else f"set {cfg_keys} in the playbook"
            raise KeyMissing(f"missing key - {where}")
        self._secrets.extend(values)
        return values

    def remember(self, secret: str) -> None:
        """Mask ``secret`` in this check's messages (for keys resolved without ``key``)."""
        if secret:
            self._secrets.append(secret)

    def scrub(self, text: str) -> str:
        """``text`` with query secrets / bearer tokens and every key of this check masked."""
        out = redact(text or "")
        for secret in sorted({s for s in self._secrets if s and len(s) >= 4}, key=len, reverse=True):
            out = out.replace(secret, "***")
            quoted = quote(secret, safe="")
            if quoted != secret:
                out = out.replace(quoted, "***")
        return out

    def key_hint(self, adapter: Any, config_key: str = "api_key", env_key: Optional[str] = None) -> str:
        """Where the key came from, for "check ..." advice ('$APOLLO_API_KEY')."""
        if str(adapter.config.get(config_key) or "").strip():
            return f"'{config_key}' in the playbook"
        env = adapter.config.get(f"{config_key}_env") or env_key or getattr(adapter, "env_key", "")
        return f"${env}" if env else "the key"

    def get(self, adapter: Any, url: str, *, params: Optional[Dict[str, Any]] = None,
            headers: Optional[Dict[str, str]] = None, hint: Optional[str] = None,
            on_status: Optional[Dict[int, str]] = None) -> Any:
        """GET ``url`` once (free endpoint, not metered) and return the decoded JSON body.

        Raises ``CheckSkipped`` in a dry run and ``CheckFailed`` for: network errors
        ("could not reach <host>"), 401 / 403 ("key rejected"), other non-2xx statuses
        and non-JSON bodies. ``on_status`` maps a status to a custom failure message.
        """
        host = _host(url)
        if getattr(self.ctx, "dry_run", False):
            found = "key found, " if self._secrets else ""
            raise CheckSkipped(f"dry run - {found}{host} not contacted")
        fingerprint = hashlib.sha256(json.dumps(
            [url, sorted((params or {}).items()), sorted((headers or {}).items())], default=str,
        ).encode("utf-8")).hexdigest()
        if fingerprint in self._answers:
            self._reused = True
            status, text = self._answers[fingerprint]
        else:
            status, text = self._request(url, params, headers)
            self._answers[fingerprint] = (status, text)
        if status == 0:
            raise CheckFailed(f"could not reach {host} ({text}) - check your internet connection / proxy")
        msg = provider_message(text)
        said = f": {msg}" if msg else ""
        if on_status and status in on_status:
            raise CheckFailed(on_status[status] + (f" (HTTP {status}{said})"))
        if status in (401, 403):
            raise CheckFailed(f"key rejected (HTTP {status}{said}) - check {hint or self.key_hint(adapter)}")
        if status == 429:
            raise CheckFailed(f"rate limited by {host} (HTTP 429{said}) - try again in a minute")
        if not 200 <= status < 300:
            extra = (" - the check endpoint may have moved: set 'doctor_url' in this entry's config"
                     if status in (404, 405) else "")
            raise CheckFailed(f"HTTP {status} from {safe_url(url)}{said}{extra}")
        try:
            return json.loads(text) if text else None
        except ValueError:
            raise CheckFailed(f"unexpected answer from {host} (not JSON)") from None

    def _request(self, url: str, params: Optional[Dict[str, Any]],
                 headers: Optional[Dict[str, str]]) -> Tuple[int, str]:
        """(status, body) - status 0 with a short reason for network errors."""
        try:
            resp = self.ctx.http.request("GET", url, params=params, headers=headers, timeout=TIMEOUT,
                                         raise_for_status=False)
        except HttpError as e:
            if e.status:  # a client that raises despite raise_for_status=False
                return e.status, e.body or ""
            reason = (e.body or "network error").split(":", 1)[0].strip() or "network error"
            return 0, reason
        return int(resp.status), str(getattr(resp, "text", "") or "")


def run_doctor(ctx: Any, google_sheet: Optional[Dict[str, Any]] = None) -> List[DoctorResult]:
    """Check every credential the playbook in ``ctx`` uses (see module docstring).

    ``google_sheet``: optional ``delivery.google_sheet`` mapping of a client file
    (``spreadsheet_id``, ``service_account_file``) - its credentials are checked too.
    """
    return Doctor(ctx).run(google_sheet=google_sheet)


# =============================================================================
# checks: fn(doctor, adapter) -> DoctorResult
# =============================================================================

def check_no_key(doctor: Doctor, adapter: Any) -> DoctorResult:
    """Offline / public adapters: nothing to test (input files must exist)."""
    kind = getattr(adapter, "adapter_kind", "")
    path = adapter.config.get("path") or adapter.config.get("file")
    if path and kind in ("source", "finder") and getattr(adapter, "offline", False):
        resolved = adapter.ctx.playbook.resolve_path(str(path))
        if not resolved.exists():
            raise CheckFailed(f"file not found: {path}")
        return result(adapter, SKIPPED, f"no key needed (file found: {path})")
    return result(adapter, SKIPPED, "no key needed")


def check_public_board(doctor: Doctor, adapter: Any) -> DoctorResult:
    return result(adapter, SKIPPED, "no key needed (public job-board API)")


def check_unknown(doctor: Doctor, adapter: Any) -> DoctorResult:
    """Plugin / unlisted network adapters: at least check that the key is set."""
    if getattr(adapter, "env_key", ""):
        doctor.key(adapter)
        return result(adapter, SKIPPED, "key is set; no live check available for this adapter type")
    return result(adapter, SKIPPED, "no live check available for this adapter type")


_POST_TARGETS: Dict[Tuple[str, str], Tuple[str, str]] = {
    ("notifier", "slack"): ("webhook_url", "SLACK_WEBHOOK_URL"),
    ("notifier", "webhook"): ("url", "LEADGEN_WEBHOOK_URL"),
    ("exporter", "webhook"): ("url", "LEADGEN_EXPORT_WEBHOOK_URL"),
}


def check_would_post(doctor: Doctor, adapter: Any) -> DoctorResult:
    """Slack / webhooks: the URL must be set, but a test would post a real message."""
    config_key, env = _POST_TARGETS.get((adapter.adapter_kind, adapter.type_name), ("url", ""))
    if not adapter.secret(config_key, env or None, required=False):
        env_name = adapter.config.get(f"{config_key}_env") or env
        where = f"${env_name} (or '{config_key}' in the playbook)" if env_name else f"'{config_key}' in the playbook"
        raise KeyMissing(f"missing URL - set {where}")
    return result(adapter, SKIPPED, "URL is set; not tested - a test would post a real message")


def check_apollo(doctor: Doctor, adapter: Any) -> DoctorResult:
    key = doctor.key(adapter)
    url = doctor_url(adapter, base_url(adapter, APOLLO_BASE) + "/v1/auth/health")
    data = doctor.get(adapter, url, headers={"x-api-key": key, "Accept": "application/json"})
    logged_in = get_path(data, "is_logged_in")
    if logged_in is True:
        return result(adapter, OK, "key accepted (Apollo reports it as logged in)")
    if logged_in is False:
        raise CheckFailed(f"key rejected - Apollo says this key is not logged in (check {doctor.key_hint(adapter)})")
    raise CheckFailed("unexpected answer from Apollo (no 'is_logged_in' field)")


def check_hunter(doctor: Doctor, adapter: Any) -> DoctorResult:
    key = doctor.key(adapter)
    url = doctor_url(adapter, base_url(adapter, HUNTER_BASE) + "/account")
    data = doctor.get(adapter, url, params={"api_key": key})
    info = get_path(data, "data")
    if not isinstance(info, dict):
        raise CheckFailed("unexpected answer from Hunter (no 'data' object)")
    parts, warnings = [], []
    needed = "verifications" if adapter.adapter_kind == "verifier" else "searches"
    for name in ("searches", "verifications", "credits"):
        q = get_path(info, f"requests.{name}")
        if not isinstance(q, dict):
            continue
        used, available = number(q.get("used")), number(q.get("available"))
        if used is None and available is None:
            continue
        parts.append(f"{name}: {fmt(used or 0)} of {fmt(available or 0)} used")
        if name in (needed, "credits") and available and used is not None and used >= available:
            warnings.append(f"no {name} left this period")
    plan, reset = info.get("plan_name"), info.get("reset_date")
    extras = ", ".join(x for x in (f"plan {plan}" if plan else "", f"resets {reset}" if reset else "") if x)
    quota = "; ".join(parts) + (f" ({extras})" if extras and parts else extras)
    detail = "key accepted" + (" - WARNING: " + ", ".join(warnings) if warnings else "")
    return result(adapter, OK, detail, quota)


def check_millionverifier(doctor: Doctor, adapter: Any) -> DoctorResult:
    key = doctor.key(adapter)
    url = doctor_url(adapter, base_url(adapter, MILLIONVERIFIER_BASE) + "/credits")
    data = doctor.get(adapter, url, params={"api": key})
    if not isinstance(data, dict):
        raise CheckFailed("unexpected answer from MillionVerifier (not an object)")
    error = data.get("error")
    if error not in (None, "", False):
        rejected = "key" in str(error).lower()
        prefix = "key rejected - " if rejected else ""
        raise CheckFailed(f"{prefix}MillionVerifier says: {error}"
                          + (f" (check {doctor.key_hint(adapter)})" if rejected else ""))
    credits = number(data.get("credits"))
    if credits is None:
        raise CheckFailed("unexpected answer from MillionVerifier (no 'credits' field)")
    return _credits_result(adapter, credits)


def _credits_result(adapter: Any, credits: float) -> DoctorResult:
    detail = "key accepted"
    if credits <= 0:
        detail += " - WARNING: no credits left, verification will fail until you top up"
    return result(adapter, OK, detail, f"{fmt(credits)} credits left")


def check_zerobounce(doctor: Doctor, adapter: Any) -> DoctorResult:
    key = doctor.key(adapter)
    url = doctor_url(adapter, base_url(adapter, ZEROBOUNCE_BASE) + "/getcredits")
    data = doctor.get(adapter, url, params={"api_key": key})
    if not isinstance(data, dict):
        raise CheckFailed("unexpected answer from ZeroBounce (not an object)")
    error = data.get("error") or data.get("Error")
    if error:
        raise CheckFailed(f"ZeroBounce says: {error}")
    credits = number(data.get("Credits", data.get("credits")))
    if credits is None:
        raise CheckFailed("unexpected answer from ZeroBounce (no 'Credits' field)")
    if credits < 0:
        raise CheckFailed("key rejected - ZeroBounce answered -1 credits, which means the API key is "
                          f"invalid (check {doctor.key_hint(adapter)})")
    return _credits_result(adapter, credits)


def check_neverbounce(doctor: Doctor, adapter: Any) -> DoctorResult:
    key = doctor.key(adapter)
    url = doctor_url(adapter, base_url(adapter, NEVERBOUNCE_BASE) + "/account/info")
    data = doctor.get(adapter, url, params={"key": key})
    if not isinstance(data, dict):
        raise CheckFailed("unexpected answer from NeverBounce (not an object)")
    status = str(data.get("status") or "").strip().lower()
    message = str(data.get("message") or "no message")
    if status == "auth_failure":
        raise CheckFailed(f"key rejected (NeverBounce: {message}) - check {doctor.key_hint(adapter)}")
    if status != "success":
        raise CheckFailed(f"NeverBounce answered '{status or 'no status'}': {message}")
    info = data.get("credits_info") if isinstance(data.get("credits_info"), dict) else {}
    paid, free = number(info.get("paid_credits_remaining")), number(info.get("free_credits_remaining"))
    parts = [f"{fmt(n)} {label} credits" for n, label in ((paid, "paid"), (free, "free")) if n is not None]
    quota = (" + ".join(parts) + " left") if parts else ""
    detail = "key accepted"
    if parts and (paid or 0) + (free or 0) <= 0:
        detail += " - WARNING: no credits left, verification will fail until you top up"
    return result(adapter, OK, detail, quota)


def check_theirstack(doctor: Doctor, adapter: Any) -> DoctorResult:
    key = doctor.key(adapter)
    parts = urlsplit(base_url(adapter, THEIRSTACK_BASE))
    default = f"{parts.scheme or 'https'}://{parts.netloc or 'api.theirstack.com'}{THEIRSTACK_CREDITS_PATH}"
    url = doctor_url(adapter, default)
    data = doctor.get(adapter, url, headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
    if not isinstance(data, dict):
        raise CheckFailed("unexpected answer from TheirStack (not an object)")
    api, used, ui = (number(data.get(k)) for k in ("api_credits", "used_api_credits", "ui_credits"))
    bits = []
    if api is not None:
        bits.append(f"API credits {fmt(api)}" + (f" ({fmt(used)} used)" if used is not None else ""))
    if ui is not None:
        bits.append(f"web-app credits {fmt(ui)}")
    return result(adapter, OK, "key accepted", ", ".join(bits))


def check_adzuna(doctor: Doctor, adapter: Any) -> DoctorResult:
    app_id, app_key = doctor.keys(adapter, [("app_id", "ADZUNA_APP_ID"), ("app_key", "ADZUNA_APP_KEY")])
    try:
        country = list(adapter.countries)[0]
    except (AttributeError, ValueError, IndexError):
        country = "gb"
    url = doctor_url(adapter, f"{base_url(adapter, ADZUNA_BASE)}/{quote(country, safe='')}/search/1")
    params = {"app_id": app_id, "app_key": app_key, "results_per_page": 1, "content-type": "application/json"}
    hint = " / ".join((doctor.key_hint(adapter, "app_id", "ADZUNA_APP_ID"),
                       doctor.key_hint(adapter, "app_key", "ADZUNA_APP_KEY")))
    data = doctor.get(adapter, url, params=params, hint=hint)
    if not isinstance(data, dict):
        raise CheckFailed("unexpected answer from Adzuna (not an object)")
    count = number(data.get("count"))
    listed = f", {fmt(count)} jobs listed" if count is not None else ""
    return result(adapter, OK, f"keys accepted (1-result test search in {country.upper()}{listed})",
                  "free API, rate-limited (no quota endpoint)")


def check_apify(doctor: Doctor, adapter: Any) -> DoctorResult:
    token = doctor.key(adapter)
    url = doctor_url(adapter, base_url(adapter, APIFY_BASE) + "/users/me")
    data = doctor.get(adapter, url, params={"token": token})
    info = get_path(data, "data")
    if not isinstance(info, dict):
        raise CheckFailed("unexpected answer from Apify (no 'data' object)")
    user = info.get("username")
    plan = info.get("plan")
    plan_name = (plan.get("id") or plan.get("description")) if isinstance(plan, dict) else plan
    monthly = number(plan.get("monthlyUsageCreditsUsd")) if isinstance(plan, dict) else None
    quota = ", ".join(x for x in (f"plan {plan_name}" if plan_name else "",
                                  f"${fmt(monthly)} platform credits per month" if monthly is not None else "")
                      if x)
    return result(adapter, OK, "token accepted" + (f" (user {user})" if user else ""), quota)


def check_anthropic(doctor: Doctor, adapter: Any) -> DoctorResult:
    from .llm.anthropic import messages_url
    from .llm.openai import clean_headers

    key = adapter.api_key()  # MissingCredentialError -> missing_key (names $ANTHROPIC_API_KEY)
    doctor.remember(key)
    model = str(adapter.model or "")
    root = messages_url(str(adapter.config.get("base_url") or ""))[:-len("/messages")]
    default = f"{root}/models/{quote(model, safe='')}" if model else f"{root}/models?limit=1"
    url = doctor_url(adapter, default).replace("{model}", quote(model, safe=""))
    headers = {"x-api-key": key,
               "anthropic-version": str(adapter.config.get("anthropic_version") or ANTHROPIC_VERSION)}
    headers.update(clean_headers(adapter.config.get("extra_headers")))
    data = doctor.get(adapter, url, headers=headers, on_status={
        404: f"key accepted, but model '{model}' was not found - check writer.model"})
    if model and isinstance(data, dict) and data.get("id"):
        shown = data.get("display_name") or data.get("id")
        return result(adapter, OK, f"key accepted; model {model} is available ({shown})")
    return result(adapter, OK, "key accepted")


def check_openai(doctor: Doctor, adapter: Any) -> DoctorResult:
    from .llm.openai import clean_headers

    adapter.check_config()  # LLMConfigError (missing base_url / model) -> "config problem"
    key = adapter.api_key()  # '' only for a keyless local server (api_key_required: false)
    doctor.remember(key)
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    headers.update(clean_headers(adapter.config.get("extra_headers")))
    url = doctor_url(adapter, adapter.base_url + "/models")
    hint = doctor.key_hint(adapter)
    data = doctor.get(adapter, url, headers=headers, hint=hint)
    items = data.get("data") if isinstance(data, dict) else data
    ids = [str(m.get("id")) for m in items if isinstance(m, dict) and m.get("id")] if isinstance(items, list) else []
    model = str(adapter.model or "")
    detail = "key accepted" if key else "server answered (no key configured)"
    if model and ids and model not in ids:
        detail += (f" - WARNING: model '{model}' is not in the list of {len(ids)} models this server "
                   f"returned; check writer.model")
    elif model and model in ids:
        detail += f"; model {model} is available"
    return result(adapter, OK, detail)


def check_instantly(doctor: Doctor, adapter: Any) -> DoctorResult:
    key = doctor.key(adapter)
    url = doctor_url(adapter, base_url(adapter, INSTANTLY_BASE) + "/api/v2/campaigns")
    doctor.get(adapter, url, params={"limit": 1}, headers={"Authorization": f"Bearer {key}"}, on_status={
        403: "key rejected or not allowed to list campaigns - if the key has limited scopes, add "
             "campaigns:read (or use an all:all key)"})
    return result(adapter, OK, "key accepted")


def check_smartlead(doctor: Doctor, adapter: Any) -> DoctorResult:
    key = doctor.key(adapter)
    url = doctor_url(adapter, base_url(adapter, SMARTLEAD_BASE) + "/campaigns")
    data = doctor.get(adapter, url, params={"api_key": key})
    campaigns = data if isinstance(data, list) else (get_path(data, "data") or get_path(data, "campaigns"))
    if not isinstance(campaigns, list):
        return result(adapter, OK, "key accepted")
    detail = f"key accepted ({len(campaigns)} campaign{'s' if len(campaigns) != 1 else ''})"
    cid = str(adapter.config.get("campaign_id") or "").strip()
    if cid:
        if any(isinstance(c, dict) and str(c.get("id")) == cid for c in campaigns):
            detail += f"; campaign {cid} found"
        else:
            detail += f" - WARNING: campaign_id {cid} is not one of this account's campaigns"
    return result(adapter, OK, detail)


def check_gsheets(doctor: Doctor, adapter: Any) -> DoctorResult:
    """Service-account credentials (no network): JSON with a client_email; gspread installed."""
    raw = adapter.config.get("service_account_json") or adapter.ctx.env.get(GOOGLE_JSON_ENV)
    if raw:
        source = "service_account_json" if adapter.config.get("service_account_json") else f"${GOOGLE_JSON_ENV}"
        if isinstance(raw, dict):
            info: Any = raw
        else:
            try:
                info = json.loads(str(raw))
            except ValueError:
                raise CheckFailed(f"{source} is not valid JSON") from None
    else:
        path = adapter.secret("service_account_file", GOOGLE_FILE_ENV, required=False)
        if not path:
            env = adapter.config.get("service_account_file_env") or GOOGLE_FILE_ENV
            raise KeyMissing(f"missing key - set ${env} to the service-account JSON file (or "
                             f"'service_account_file' in the config, or ${GOOGLE_JSON_ENV})")
        resolved = adapter.ctx.playbook.resolve_path(str(path))
        source = str(resolved)
        if not resolved.is_file():
            raise CheckFailed(f"service-account file not found: {resolved}")
        try:
            info = json.loads(Path(resolved).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            raise CheckFailed(f"service-account file is not valid JSON: {resolved}") from None
    if not isinstance(info, dict):
        raise CheckFailed(f"{source} must be a JSON object (a service-account key)")
    kind = info.get("type")
    email = str(info.get("client_email") or "").strip()
    if kind and kind != "service_account":
        raise CheckFailed(f"{source} is a '{kind}' file, not a service-account key - in Google Cloud create "
                          f"a service account and download a JSON key")
    if not email:
        raise CheckFailed(f"{source} has no 'client_email' - download a service-account JSON key from "
                          f"Google Cloud (IAM > Service accounts > Keys)")
    if not _gspread_installed():
        raise CheckFailed(f"service account {email} looks fine, but the gspread package is not installed "
                          f"- run: pip install gspread")
    detail = f"service account {email} (not contacted - share the sheet with this address as Editor)"
    if not (adapter.config.get("spreadsheet_id") or adapter.config.get("spreadsheet_url")):
        detail += " - WARNING: no spreadsheet_id set"
    return result(adapter, OK, detail)


class _SheetCredentials(Adapter):
    """Stand-in adapter for a client's ``delivery.google_sheet`` push (credentials only)."""

    name = "google_sheet"
    env_key = GOOGLE_FILE_ENV
    offline = True


# (kind, type) -> check. Plugins may add their own: CHECKS[("source", "mine")] = fn.
CHECKS: Dict[Tuple[str, str], CheckFn] = {
    ("source", "apollo"): check_apollo,
    ("finder", "apollo"): check_apollo,
    ("finder", "hunter"): check_hunter,
    ("verifier", "hunter"): check_hunter,
    ("verifier", "millionverifier"): check_millionverifier,
    ("verifier", "zerobounce"): check_zerobounce,
    ("verifier", "neverbounce"): check_neverbounce,
    ("source", "theirstack"): check_theirstack,
    ("source", "adzuna"): check_adzuna,
    ("source", "apify"): check_apify,
    ("source", "linkedin_jobs"): check_apify,
    ("llm", "anthropic"): check_anthropic,
    ("llm", "openai"): check_openai,
    ("llm", "openai_compatible"): check_openai,
    ("exporter", "instantly"): check_instantly,
    ("exporter", "smartlead"): check_smartlead,
    ("exporter", "gsheets"): check_gsheets,
    ("delivery", "google_sheet"): check_gsheets,
    ("notifier", "slack"): check_would_post,
    ("notifier", "webhook"): check_would_post,
    ("exporter", "webhook"): check_would_post,
    ("source", "greenhouse"): check_public_board,
    ("source", "lever"): check_public_board,
    ("source", "ashby"): check_public_board,
    ("source", "csv"): check_no_key,
    ("source", "json"): check_no_key,
    ("finder", "pattern"): check_no_key,
    ("finder", "csv"): check_no_key,
    ("verifier", "basic"): check_no_key,
    ("notifier", "console"): check_no_key,
    ("exporter", "csv"): check_no_key,
    ("exporter", "json"): check_no_key,
    ("exporter", "instantly_csv"): check_no_key,
    ("exporter", "smartlead_csv"): check_no_key,
}


__all__ = ["CHECKS", "CheckFailed", "CheckSkipped", "Doctor", "DoctorResult", "FAILED", "KeyMissing",
           "MISSING_KEY", "OK", "SKIPPED", "STATUSES", "format_results", "run_doctor"]
