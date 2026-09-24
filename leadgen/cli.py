"""Command-line interface: ``leadgen <command> [options]`` (also ``python -m leadgen``).

Commands
--------
init NAME       Create ``playbooks/NAME.yaml`` from a template (never overwrites).
validate        Load a playbook, build every adapter it uses and report unknown
                types, config mistakes and missing API keys as a checklist.
run             One full pipeline run: find -> filter -> enrich -> verify ->
                score -> write -> export. ``--dry-run`` skips everything that
                uses the network (no paid calls, nothing sent).
demo            Write the "live opportunities" one-pager (Markdown + HTML) for a run.
leads           Show the leads of a run as a table.
replies         Classify a replies CSV and act on every reply (stages, suppression,
                follow-ups, alerts); writes ``replies_classified.csv``.
serve           Run the webhook receiver for Instantly / Smartlead reply events.
stats           Funnel numbers + recent runs.
mark            Move a lead to a stage by hand (booked, won, lost, ...).
suppress        Manage the do-not-contact list (add / remove / list).
followups       Show follow-ups that are due; ``--done ID`` ticks one off.
adapters        List every adapter type the engine knows, per kind.

Global options (accepted before or after the command)
-----------------------------------------------------
-p / --playbook PATH   The playbook YAML (required by run, validate, demo, replies,
                       serve; optional elsewhere, where it scopes the output).
--db PATH              SQLite file; overrides the playbook's ``storage.path``
                       (default without a playbook: ``data/leadgen.db``).
-v / --verbose         More logging (-v info, -vv debug) and full tracebacks.
--env-file PATH        ``KEY=VALUE`` file loaded before anything else (default
                       ``.env`` in the current directory, skipped when missing).
                       Variables already set in the environment always win.

Plugins: ``LEADGEN_PLUGINS=my_pkg.adapters,other_module`` (environment or .env)
imports those modules before any command runs, so they can add their own
adapter types with ``leadgen.registry.register(kind, type, "module:Class")``.
The working directory is importable for this.

Exit codes: 0 success, 1 the command ran but found a problem (validation
failed, every source failed, nothing to show), 2 usage / configuration error
(bad playbook, missing file or credential) - reported as one friendly line on
stderr, with the traceback only under ``-v``.
"""
from __future__ import annotations

import argparse
import csv
import importlib
import logging
import os
import re
import sqlite3
import sys
import traceback
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import yaml

from . import __version__, registry
from .context import Context, MissingCredentialError
from .http import HttpClient
from .models import Lead, ReplyCategory, Stage, Tier
from .playbook import DEFAULTS, Playbook, PlaybookError, from_dict, load_playbook
from .store import Store
from .utils import parse_date

EXIT_OK = 0
EXIT_PROBLEM = 1
EXIT_USAGE = 2

TEMPLATES = ("generic", "recruitment", "saas-funding", "local-business", "agency-outreach")
DEFAULT_ENV_FILE = ".env"
DEFAULT_DB = str(DEFAULTS["storage"]["path"])
WEBHOOK_TOKEN_ENV = "LEADGEN_WEBHOOK_TOKEN"
PLUGINS_ENV = "LEADGEN_PLUGINS"

_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

log = logging.getLogger("leadgen")


class CliError(Exception):
    """A problem to report as one friendly line (exit ``code``)."""

    def __init__(self, message: str, code: int = EXIT_USAGE):
        super().__init__(message)
        self.code = code


# =============================================================================
# .env files
# =============================================================================

def parse_env_text(text: str) -> Tuple[Dict[str, str], List[str]]:
    """Parse ``KEY=VALUE`` lines (a tiny, dependency-free ``.env`` reader).

    Supports ``# comments``, blank lines, an optional ``export`` prefix,
    single- or double-quoted values (double quotes understand ``\\n``, ``\\t``,
    ``\\"`` and ``\\\\``) and trailing `` # comments`` after unquoted values.
    Returns ``(values, warnings)``; malformed lines are skipped with a warning.
    """
    values: Dict[str, str] = {}
    warnings: List[str] = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export ") or line.startswith("export\t"):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not _ENV_KEY_RE.match(key):
            warnings.append(f"line {n}: expected KEY=VALUE, got {raw.strip()[:40]!r}")
            continue
        value = value.strip()
        if value[:1] not in ("'", '"'):
            # unquoted: a "#" at the start or after whitespace starts a comment
            value = re.split(r"(?:^|\s)#", value, maxsplit=1)[0].rstrip()
        else:
            quote = value[0]
            end = value.find(quote, 1)
            if quote == '"':
                # find the closing quote that is not escaped
                end, i = -1, 1
                while i < len(value):
                    if value[i] == "\\":
                        i += 2
                        continue
                    if value[i] == '"':
                        end = i
                        break
                    i += 1
            if end == -1:
                warnings.append(f"line {n}: unterminated quote for {key}")
                continue
            inner = value[1:end]
            if quote == '"':
                inner = re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t", "r": "\r"}.get(m.group(1), m.group(1)),
                               inner)
            value = inner
        values[key] = value
    return values, warnings


def load_env_file(path: Any, env: Dict[str, str], required: bool = False) -> List[str]:
    """Merge ``path`` into ``env`` without overriding keys already present.

    Returns the keys that were added. A missing file is an error only when
    ``required`` (i.e. the user named it explicitly)."""
    p = Path(os.path.expanduser(str(path)))
    if not p.exists() or p.is_dir():
        if required:
            raise FileNotFoundError(2, "env file not found", str(p))
        return []
    values, warnings = parse_env_text(p.read_text(encoding="utf-8-sig"))
    for w in warnings:
        log.warning("%s: %s (ignored)", p, w)
    added = []
    for k, v in values.items():
        if k not in env:
            env[k] = v
            added.append(k)
    log.debug("loaded %d variable(s) from %s", len(added), p)
    return added


# =============================================================================
# logging + output helpers
# =============================================================================

class _StderrHandler(logging.Handler):
    """Writes to the *current* ``sys.stderr`` (so redirection/capture works)."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            sys.stderr.write(self.format(record) + "\n")
        except Exception:  # noqa: BLE001 - logging must never raise
            self.handleError(record)


def setup_logging(verbosity: int) -> Callable[[], None]:
    """``leadgen`` logger -> stderr: warnings by default, -v info, -vv debug.

    Returns a function that restores the logger's previous level and handlers
    (``main`` calls it on exit, so embedding the CLI leaves logging untouched)."""
    level = logging.WARNING if verbosity <= 0 else logging.INFO if verbosity == 1 else logging.DEBUG
    logger = logging.getLogger("leadgen")
    previous_level = logger.level
    for h in list(logger.handlers):
        if getattr(h, "_leadgen_cli", False):
            logger.removeHandler(h)
    handler = _StderrHandler()
    handler._leadgen_cli = True  # type: ignore[attr-defined]
    fmt = "%(levelname)s %(name)s: %(message)s" if verbosity >= 2 else "%(levelname)s: %(message)s"
    handler.setFormatter(_LowerLevelFormatter(fmt))
    handler.setLevel(level)
    logger.addHandler(handler)
    logger.setLevel(level)

    def restore() -> None:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    return restore


class _LowerLevelFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        saved = record.levelname
        record.levelname = saved.lower()
        try:
            return super().format(record)
        finally:
            record.levelname = saved


def _out(text: str = "") -> None:
    sys.stdout.write(text + "\n")


def _clip(value: Any, width: int) -> str:
    s = " ".join(str("" if value is None else value).split())
    return s if len(s) <= width else s[: max(0, width - 1)] + "…"


def format_table(headers: Sequence[str], rows: Iterable[Sequence[Any]],
                 widths: Optional[Dict[str, int]] = None, right: Sequence[str] = ()) -> str:
    """Plain aligned text table; ``widths`` caps (and clips) named columns."""
    widths = widths or {}
    cells = [[_clip(v, widths.get(h, 60)) for h, v in zip(headers, row)] for row in rows]
    col_w = [len(h) for h in headers]
    for row in cells:
        for i, v in enumerate(row):
            col_w[i] = max(col_w[i], len(v))

    def line(values: Sequence[str]) -> str:
        parts = []
        for i, v in enumerate(values):
            parts.append(v.rjust(col_w[i]) if headers[i] in right else v.ljust(col_w[i]))
        return "  ".join(parts).rstrip()

    out = [line(list(headers)), line(["-" * w for w in col_w])]
    out += [line(r) for r in cells]
    return "\n".join(out)


# =============================================================================
# session: playbook + store + context
# =============================================================================

@dataclass
class Session:
    """What a command works with; ``close()`` releases the database."""

    ctx: Context
    store: Store
    playbook: Optional[Playbook]      # None when the command ran without -p
    playbook_path: Optional[str]

    @property
    def scope(self) -> Optional[str]:
        """Playbook name used to scope store queries (None = all playbooks)."""
        return self.playbook.name if self.playbook else None

    def close(self) -> None:
        for closer in (self.store.close, getattr(getattr(self.ctx.http, "session", None), "close", None)):
            try:
                if closer:
                    closer()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass


def build_env(args: argparse.Namespace) -> Dict[str, str]:
    """A copy of ``os.environ`` plus the ``--env-file`` values (existing vars win).
    Computed once per command and cached on ``args``."""
    cached = getattr(args, "_env", None)
    if cached is not None:
        return cached
    env = dict(os.environ)
    explicit = getattr(args, "env_file_explicit", False)
    path = getattr(args, "env_file", DEFAULT_ENV_FILE) or ""
    if path:
        load_env_file(path, env, required=explicit)
    args._env = env
    return env


def load_plugins(env: Dict[str, str]) -> List[str]:
    """Import the modules named in ``$LEADGEN_PLUGINS`` (comma-separated); they
    register extra adapter types on import. Returns the imported module names."""
    names = [n.strip() for n in (env.get(PLUGINS_ENV) or "").split(",") if n.strip()]
    if names and os.getcwd() not in sys.path:
        sys.path.insert(0, os.getcwd())
    for name in names:
        try:
            importlib.import_module(name)
        except Exception as e:  # noqa: BLE001 - report any import problem as one line
            raise CliError(f"cannot import plugin {name!r} from ${PLUGINS_ENV}: {type(e).__name__}: {e}") from e
        log.debug("loaded plugin %s", name)
    return names


def open_session(args: argparse.Namespace, *, need_playbook: bool, dry_run: bool = False) -> Session:
    """Load the playbook (if any), open the store and build the ``Context``."""
    env = build_env(args)
    pb_path = getattr(args, "playbook", None)
    playbook: Optional[Playbook] = None
    if pb_path:
        playbook = load_playbook(pb_path, env=env)
    elif need_playbook:
        raise CliError(f"'{args.command}' needs a playbook: add -p playbooks/<name>.yaml "
                       f"(try: leadgen {args.command} -p playbooks/demo-offline.yaml)")
    db = getattr(args, "db", None) or (str(playbook.db_path) if playbook else DEFAULT_DB)
    try:
        store = Store(os.path.expanduser(db))
    except (sqlite3.Error, OSError) as e:
        raise CliError(f"cannot open the database {db}: {e}") from e
    ctx = Context(playbook=playbook or from_dict({"name": "leadgen"}, env=env), http=HttpClient(),
                  store=store, env=env, today=date.today(), log=log, dry_run=dry_run)
    return Session(ctx=ctx, store=store, playbook=playbook, playbook_path=pb_path)


def _resolve_run(session: Session, run: Optional[str]) -> str:
    """``latest`` (default) -> newest finished run of the playbook (or of any playbook)."""
    store = session.store
    if run and run != "latest":
        if not store.conn.execute("SELECT 1 FROM runs WHERE id=?", (run,)).fetchone():
            raise CliError(f"run {run!r} not found in {store.path} (see: leadgen stats)", EXIT_PROBLEM)
        return run
    if session.playbook:
        rid = store.latest_run_id(session.playbook.name)
    else:
        rid = next((r["id"] for r in store.list_runs(None, limit=50) if r.get("finished_at")), None)
    if not rid:
        who = f"playbook '{session.playbook.name}'" if session.playbook else "any playbook"
        hint = f"leadgen run -p {session.playbook_path}" if session.playbook_path else "leadgen run -p <playbook>"
        raise CliError(f"no finished runs for {who} in {store.path} yet - run `{hint}` first", EXIT_PROBLEM)
    return rid


def _pb_hint(session: Session) -> str:
    return f" -p {session.playbook_path}" if session.playbook_path else ""


# =============================================================================
# init
# =============================================================================

def find_templates_dir() -> Path:
    """``playbooks/templates`` in the working dir, else next to the installed package."""
    candidates = [Path.cwd() / "playbooks" / "templates",
                  Path(__file__).resolve().parent.parent / "playbooks" / "templates"]
    for c in candidates:
        if c.is_dir():
            return c
    raise CliError("playbook templates not found (expected playbooks/templates/ in the working "
                   "directory or the leadgen source checkout)")


def cmd_init(args: argparse.Namespace) -> int:
    name = args.name.strip()
    if name.endswith((".yaml", ".yml")):
        name = name.rsplit(".", 1)[0]
    if not _NAME_RE.match(name):
        raise CliError(f"invalid playbook name {args.name!r}: use letters, digits, '-', '_' and '.' only")
    template = find_templates_dir() / f"{args.template}.yaml"
    if not template.is_file():
        raise CliError(f"template {args.template!r} not found at {template}")
    dest = Path(args.dir) / f"{name}.yaml"
    if dest.exists():
        raise CliError(f"{dest} already exists - choose another name or delete that file first",
                       EXIT_PROBLEM)
    text = template.read_text(encoding="utf-8")
    new_text, n = re.subn(r"(?m)^name:[ \t]*\S.*$", f"name: {name}", text, count=1)
    if not n:
        new_text = f"name: {name}\n" + text
    from_dict(yaml.safe_load(new_text) or {}, env={})  # the copy must still be a valid playbook
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(new_text, encoding="utf-8")
    _out(f"Created {dest} from the '{args.template}' template.")
    _out("")
    _out("Next steps:")
    _out(f"  1. Edit {dest} - every section is commented (offer, icp, signals, buyers, sources, ...).")
    _out(f"  2. leadgen validate -p {dest}     # checks adapters + API keys")
    _out(f"  3. leadgen run -p {dest} --dry-run  # offline rehearsal, no paid calls")
    _out(f"  4. leadgen run -p {dest}")
    return EXIT_OK


# =============================================================================
# validate
# =============================================================================

@dataclass
class Check:
    status: str          # ok | warn | fail | skip
    label: str
    detail: str = ""


# Adapters whose credentials are not the default ``api_key`` / ``env_key`` pair:
# (kind, type) -> [(config_key, env_var), ...] (all required).
CREDENTIALS: Dict[Tuple[str, str], List[Tuple[str, str]]] = {
    ("source", "adzuna"): [("app_id", "ADZUNA_APP_ID"), ("app_key", "ADZUNA_APP_KEY")],
    ("notifier", "slack"): [("webhook_url", "SLACK_WEBHOOK_URL")],
    ("notifier", "webhook"): [("url", "LEADGEN_WEBHOOK_URL")],
    ("exporter", "webhook"): [("url", "LEADGEN_EXPORT_WEBHOOK_URL")],
}

# Config keys an adapter cannot work without: (kind, type) -> [key | (any, of, these)].
REQUIRED_CONFIG: Dict[Tuple[str, str], List[Any]] = {
    ("exporter", "instantly"): ["campaign_id"],
    ("exporter", "smartlead"): ["campaign_id"],
    ("exporter", "gsheets"): [("spreadsheet_id", "spreadsheet_url")],
    ("source", "apify"): [("actor", "dataset_id")],
    ("source", "greenhouse"): [("companies", "board")],
    ("source", "lever"): [("companies", "board")],
    ("source", "ashby"): [("companies", "board")],
    ("source", "csv"): [("path", "file")],
    ("source", "json"): [("path", "file")],
    ("finder", "csv"): ["path"],
}


def credential_problems(kind: str, adapter: Any) -> List[str]:
    """Missing credentials of one adapter, as readable hints ('' list = fine)."""
    name = str(adapter.config.get("type") or getattr(adapter, "name", "?"))
    hook = getattr(adapter, "required_credentials", None)
    if callable(hook):  # adapters may describe themselves: [(config_key, env_var), ...]
        pairs = list(hook())
    elif (kind, name) in CREDENTIALS:
        pairs = CREDENTIALS[(kind, name)]
    elif kind == "exporter" and name == "gsheets":
        cfg, env = adapter.config, adapter.ctx.env
        ok = (cfg.get("service_account_json") or env.get("GOOGLE_SERVICE_ACCOUNT_JSON")
              or adapter.has_secret("service_account_file", "GOOGLE_APPLICATION_CREDENTIALS"))
        return [] if ok else ["missing Google credentials (set $GOOGLE_APPLICATION_CREDENTIALS to a "
                              "service-account JSON file, or $GOOGLE_SERVICE_ACCOUNT_JSON)"]
    elif kind == "llm" and callable(getattr(adapter, "api_key", None)):
        try:
            adapter.api_key()
        except MissingCredentialError as e:
            return [str(e)]
        return []
    elif getattr(adapter, "env_key", "") and not getattr(adapter, "offline", False):
        pairs = [("api_key", adapter.env_key)]
    else:
        pairs = []
    problems = []
    for config_key, env_var in pairs:
        if not adapter.has_secret(config_key, env_var):
            where = adapter.config.get(f"{config_key}_env") or env_var
            problems.append(f"missing credential: set ${where} (or '{config_key}' in the playbook)")
    return problems


def config_problems(kind: str, adapter: Any) -> List[str]:
    """Missing required config keys, missing input files and adapter self-checks."""
    cfg = adapter.config
    name = str(cfg.get("type") or "")
    problems = []
    for req in REQUIRED_CONFIG.get((kind, name), []):
        keys = req if isinstance(req, tuple) else (req,)
        if not any(cfg.get(k) not in (None, "", [], {}) for k in keys):
            problems.append("missing '" + "' or '".join(keys) + "' in its config")
    path = cfg.get("path") or cfg.get("file")
    if path and getattr(adapter, "offline", False):
        resolved = adapter.ctx.playbook.resolve_path(str(path))
        if not resolved.exists():
            problems.append(f"file not found: {path}")
    for hook_name in ("validate", "check_config"):
        hook = getattr(adapter, hook_name, None)
        if callable(hook):
            try:
                hook()
            except MissingCredentialError:
                pass  # reported by credential_problems
            except Exception as e:  # noqa: BLE001 - config errors of any type
                problems.append(str(e) or type(e).__name__)
    return problems


def _describe(kind: str, cfg: Dict[str, Any]) -> str:
    label = cfg.get("label")
    t = str(cfg.get("type") or "?")
    extra = f" '{label}'" if label and label != t else ""
    path = cfg.get("path")
    return f"{kind} {t}{extra}" + (f" ({path})" if path else "")


def check_adapter(kind: str, cfg: Any, ctx: Context) -> Check:
    """Build one adapter and check its config + credentials (no network calls)."""
    if not isinstance(cfg, dict):
        return Check("fail", f"{kind} ?", f"expected a mapping with a 'type', got {cfg!r}")
    label = _describe(kind, cfg)
    if cfg.get("enabled") is False:
        return Check("skip", label, "disabled (enabled: false)")
    try:
        adapter = registry.create(kind, cfg, ctx)
    except registry.UnknownAdapterError as e:
        return Check("fail", label, str(e.args[0] if e.args else e))
    except Exception as e:  # noqa: BLE001 - import error, bad config in __init__
        return Check("fail", label, f"cannot be created: {e}")
    problems = config_problems(kind, adapter)
    creds = credential_problems(kind, adapter)
    offline = bool(getattr(adapter, "offline", False))
    if creds and ctx.dry_run and not offline:
        return Check("warn" if not problems else "fail", label,
                     "; ".join(problems + [c + " - skipped in --dry-run" for c in creds]))
    problems += creds
    if problems:
        return Check("fail", label, "; ".join(problems))
    note = "offline" if offline else ("network: skipped in --dry-run" if ctx.dry_run else "network")
    return Check("ok", label, note)


def validate_playbook(ctx: Context) -> List[Check]:
    """Checklist for every adapter the playbook uses, plus a few sanity checks."""
    pb = ctx.playbook
    checks: List[Check] = []
    active_sources = [s for s in pb.sources if not (isinstance(s, dict) and s.get("enabled") is False)]
    if not active_sources:
        checks.append(Check("fail", "sources", "no sources configured - nothing to find (add one under 'sources:')"))
    for cfg in pb.sources:
        checks.append(check_adapter("source", cfg, ctx))
    finders = pb.enrichment.get("finders") or []
    if not finders:
        checks.append(Check("warn", "finders", "no contact finders - only contacts supplied by sources are used"))
    for cfg in finders:
        checks.append(check_adapter("finder", cfg, ctx))
    ver = pb.enrichment.get("verifier")
    if ver:
        checks.append(check_adapter("verifier", ver, ctx))
    else:
        checks.append(Check("warn", "verifier", "none configured - emails are not checked before sending"))
    w = pb.writer
    if w.get("type") == "ai":
        llm_cfg = {"type": w.get("provider"), "model": w.get("model"), "base_url": w.get("base_url"),
                   "temperature": w.get("temperature")}
        if w.get("api_key_env"):
            llm_cfg["api_key_env"] = w["api_key_env"]
        c = check_adapter("llm", llm_cfg, ctx)
        c.label = f"writer ai ({w.get('provider')}{', ' + str(w.get('model')) if w.get('model') else ''})"
        if c.status == "fail" and w.get("fallback_to_template", True):
            c.detail += " - the template writer would be used instead"
        checks.append(c)
    else:
        checks.append(check_adapter("writer", dict(w, type="template"), ctx))
    exporters = pb.outbound.get("exporters") or []
    if not exporters:
        checks.append(Check("warn", "exporters", "none configured - results only land in the database"))
    for cfg in exporters:
        checks.append(check_adapter("exporter", cfg, ctx))
    for cfg in pb.notify.get("channels") or []:
        checks.append(check_adapter("notifier", cfg, ctx))
    if pb.replies.get("classifier") == "ai" and w.get("type") != "ai" and not w.get("provider"):
        checks.append(Check("warn", "replies", "classifier 'ai' needs writer.provider - the rules are used instead"))
    if not (pb.buyers.get("titles") or []):
        checks.append(Check("warn", "buyers", "buyers.titles is empty - any contact counts as a decision-maker"))
    if not pb.offer.get("sender_name"):
        checks.append(Check("warn", "offer", "offer.sender_name is empty - emails would go out unsigned"))
    return checks


_MARK = {"ok": "[ok]  ", "warn": "[warn]", "fail": "[FAIL]", "skip": "[skip]"}


def cmd_validate(args: argparse.Namespace) -> int:
    session = open_session(args, need_playbook=True, dry_run=args.dry_run)
    try:
        pb = session.ctx.playbook
        _out(f"Playbook: {pb.name}  ({session.playbook_path})")
        if pb.description:
            _out(f"  {pb.description}")
        if args.dry_run:
            _out("  (checking for --dry-run: network adapters are skipped, their keys are optional)")
        _out("")
        checks = validate_playbook(session.ctx)
        for c in checks:
            _out(f"  {_MARK.get(c.status, c.status)} {c.label}" + (f" - {c.detail}" if c.detail else ""))
        fails = [c for c in checks if c.status == "fail"]
        warns = [c for c in checks if c.status == "warn"]
        _out("")
        if fails:
            _out(f"{len(fails)} problem(s), {len(warns)} warning(s). Fix the [FAIL] lines above "
                 f"(API keys go in .env - see .env.example).")
            return EXIT_PROBLEM
        uses_network = any(c.status == "ok" and c.detail.startswith("network") for c in checks)
        _out(f"OK - ready to run ({len(warns)} warning(s)). Next: leadgen run -p {session.playbook_path}"
             + (" --dry-run" if uses_network or args.dry_run else ""))
        return EXIT_OK
    finally:
        session.close()


# =============================================================================
# run / demo / leads
# =============================================================================

def _console_shows(pb: Playbook, event: str) -> bool:
    """True when a console notifier will already print ``event`` to stdout."""
    if event not in (pb.notify.get("on") or []):
        return False
    for ch in pb.notify.get("channels") or []:
        if not isinstance(ch, dict) or ch.get("type") != "console" or ch.get("enabled") is False:
            continue
        if str(ch.get("stream") or "stdout").lower() != "stdout":
            continue
        events = ch.get("events")
        if not events or event in events:
            return True
    return False


def _failed_sources(pb: Playbook, errors: Sequence[str], dry_run: bool) -> Tuple[int, int]:
    """(sources that should have run, sources that failed). In a dry run only the
    offline sources run, so only those count."""
    active = [s for s in pb.sources if isinstance(s, dict) and s.get("enabled") is not False]
    if dry_run:
        def runs_offline(cfg: Dict[str, Any]) -> bool:
            try:
                return bool(getattr(registry.resolve("source", str(cfg.get("type"))), "offline", False))
            except Exception:  # noqa: BLE001 - unknown type: it "ran" and failed
                return True
        active = [s for s in active if runs_offline(s)]
    failed = 0
    for cfg in active:
        label = cfg.get("label") or cfg.get("type")
        if any(e.startswith(f"source {label}:") for e in errors):
            failed += 1
    return len(active), failed


def cmd_run(args: argparse.Namespace) -> int:
    from .pipeline import Pipeline

    if args.limit is not None and args.limit < 1:
        raise CliError("--limit must be a positive number")
    session = open_session(args, need_playbook=True, dry_run=args.dry_run)
    try:
        ctx = session.ctx
        pb = ctx.playbook
        mode = " (dry run: nothing that uses the network runs, nothing is sent)" if args.dry_run else ""
        _out(f"Running playbook '{pb.name}'{mode} ...")
        result = Pipeline(ctx, out_dir=Path(args.out) if args.out else None, limit=args.limit).run()
        if not _console_shows(pb, "run_summary"):
            _out(result.summary())
        _out("")
        _out(f"Files in {result.out_dir}:")
        for name, what in (("opportunities.csv", "every lead with score, reasons and emails (review / Google Sheets)"),
                           ("instantly_upload.csv", "ready to import into Instantly"),
                           ("smartlead_upload.csv", "ready to import into Smartlead"),
                           ("leads.json", "everything, for other tools"),
                           ("rejected.csv", "companies filtered out, with the reason"),
                           ("summary.json", "run counts + top leads")):
            if (result.out_dir / name).exists():
                _out(f"  {name:<22} {what}")
        _out("")
        _out("Next:")
        _out(f"  leadgen leads{_pb_hint(session)}      # the leads of this run")
        _out(f"  leadgen demo{_pb_hint(session)}       # one-page 'live opportunities' report")
        if args.dry_run and not result.counts.get("sourced"):
            _out("")
            _out("Note: nothing was found because every source uses the network and --dry-run skips "
                 "them. Add a csv/json source to rehearse offline, or run without --dry-run.")
        active, failed = _failed_sources(pb, result.errors, args.dry_run)
        if active and failed >= active:
            sys.stderr.write(f"error: every source failed ({failed} of {active}) - see the errors above\n")
            return EXIT_PROBLEM
        if not active:
            sys.stderr.write("warning: the playbook has no sources configured - nothing was found\n")
        return EXIT_OK
    finally:
        session.close()


def cmd_demo(args: argparse.Namespace) -> int:
    from .report import write_demo

    if args.top < 0:
        raise CliError("--top must be 0 (all) or a positive number")
    session = open_session(args, need_playbook=True)
    try:
        run_id = _resolve_run(session, args.run)
        leads = session.store.leads_for_run(run_id)
        if args.out:
            out = Path(args.out)
        else:  # the folder the run itself wrote to (recorded in the run's meta)
            meta = next((r["meta"] for r in session.ctx.store.list_runs(session.ctx.playbook.name, limit=1000)
                         if r["id"] == run_id), {}) or {}
            base = Path(meta["out_dir"]) if meta.get("out_dir") else Path("output") / session.ctx.playbook.name
            out = base / run_id
        md, page = write_demo(leads, session.ctx, out, top=args.top, prospect=args.prospect,
                              mask=not args.no_mask)
        _out(f"Demo report for run {run_id} ({len(leads)} leads in the run):")
        _out(f"  {md}")
        _out(f"  {page}   <- open in a browser, or print to PDF")
        if not args.no_mask:
            _out("Emails are masked; add --no-mask to show them in full.")
        return EXIT_OK
    finally:
        session.close()


def _lead_row(ld: Lead) -> List[Any]:
    ct = ld.contact
    who = ""
    if ct:
        who = ct.full_name or ct.email
        if ct.title:
            who = f"{who} ({ct.title})" if who else ct.title
    email = f"{ct.email} [{ct.email_status}]" if ct and ct.email else "-"
    sig = ld.top_signal
    return [ld.score, ld.tier, ld.stage, ld.company.name, who or "-", email, sig.title if sig else "-"]


def cmd_leads(args: argparse.Namespace) -> int:
    session = open_session(args, need_playbook=False)
    try:
        if args.run == "all":
            leads = session.store.list_leads(session.scope, limit=100000)
            where = f"all runs of {session.scope}" if session.scope else "all playbooks"
        else:
            run_id = _resolve_run(session, args.run)
            leads = session.store.leads_for_run(run_id)
            where = f"run {run_id}"
        if args.tier:
            leads = [ld for ld in leads if ld.tier == args.tier]
        total = len(leads)
        leads = leads[: args.limit] if args.limit and args.limit > 0 else leads
        if not leads:
            _out(f"No leads in {where}" + (f" with tier '{args.tier}'" if args.tier else "") + ".")
            return EXIT_OK
        _out(f"Leads in {where}" + (f" (tier {args.tier})" if args.tier else "")
             + f": showing {len(leads)} of {total}")
        _out("")
        _out(format_table(["score", "tier", "stage", "company", "contact", "email", "top signal"],
                          [_lead_row(ld) for ld in leads],
                          widths={"company": 28, "contact": 38, "email": 46, "top signal": 40},
                          right=("score",)))
        return EXIT_OK
    finally:
        session.close()


# =============================================================================
# replies / serve
# =============================================================================

REPLY_COLUMNS = ["received_at", "from_email", "subject", "category", "confidence", "summary",
                 "follow_up_date", "referral_name", "referral_email", "lead_id", "company", "action",
                 "suggested_reply", "body", "classifier"]


def cmd_replies(args: argparse.Namespace) -> int:
    from .outbound.csv_export import write_csv
    from .replies import handle_reply, load_replies_csv

    session = open_session(args, need_playbook=True)
    try:
        ctx = session.ctx
        path = Path(args.file)
        if not path.is_file():
            raise FileNotFoundError(2, "replies file not found", str(path))
        replies = load_replies_csv(path)
        if not replies:
            _out(f"No replies found in {path} (expected columns like from_email, subject, body).")
            return EXIT_PROBLEM
        handled = []
        failed = 0
        for r in replies:
            try:
                handled.append(handle_reply(r, ctx))
            except Exception as e:  # noqa: BLE001 - one odd reply must not stop the batch
                failed += 1
                log.error("reply from %s could not be processed: %s", r.from_email or "?", e)
                r.action = f"error: {e}"
                handled.append(r)
        companies: Dict[str, str] = {}
        for r in handled:
            if r.lead_id and r.lead_id not in companies:
                ld = session.store.get_lead(r.lead_id)
                companies[r.lead_id] = ld.company.name if ld else ""
        out_dir = Path(args.out) if args.out else Path("output") / ctx.playbook.name
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "replies_classified.csv"
        rows = []
        for r in handled:
            d = r.to_dict()
            d["company"] = companies.get(r.lead_id, "")
            d["confidence"] = round(float(r.confidence or 0), 2)
            rows.append([d.get(c, "") if d.get(c) is not None else "" for c in REPLY_COLUMNS])
        write_csv(out_path, REPLY_COLUMNS, rows, guard=True)
        _out("")
        _out(f"Classified {len(handled)} replies from {path}:")
        _out("")
        _out(format_table(["from", "category", "conf", "company", "action"],
                          [[r.from_email, r.category, f"{float(r.confidence or 0):.2f}",
                            companies.get(r.lead_id, "") or "-", r.action] for r in handled],
                          widths={"from": 38, "company": 24, "action": 70}, right=("conf",)))
        counts: Dict[str, int] = {}
        for r in handled:
            counts[r.category] = counts.get(r.category, 0) + 1
        _out("")
        _out("By category: " + ", ".join(f"{c} {counts[c]}" for c in ReplyCategory.ALL if c in counts))
        _out(f"Written: {out_path}")
        _out(f"Next: leadgen stats{_pb_hint(session)}  |  leadgen followups{_pb_hint(session)} --days 30")
        if failed:
            sys.stderr.write(f"error: {failed} of {len(replies)} replies could not be processed (see above)\n")
            return EXIT_PROBLEM
        return EXIT_OK
    finally:
        session.close()


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import WEBHOOK_PATHS, make_server

    session = open_session(args, need_playbook=True)
    token = args.token or session.ctx.env.get(WEBHOOK_TOKEN_ENV) or None
    try:
        try:
            server = make_server(session.ctx, args.host, args.port, token=token)
        except OSError as e:
            raise CliError(f"cannot listen on {args.host}:{args.port}: {e.strerror or e}") from None
        url = server.url
        _out(f"leadgen webhook server for playbook '{session.ctx.playbook.name}' on {url}")
        _out(f"  health:   GET  {url}/health")
        for p in sorted(WEBHOOK_PATHS):
            _out(f"  webhook:  POST {url}{p}" + ("?token=<token>" if token else ""))
        if token:
            _out("  auth:     token required (header X-Leadgen-Token or ?token=)")
        else:
            _out("  auth:     none - set --token or $LEADGEN_WEBHOOK_TOKEN before exposing this server")
            if args.host not in ("127.0.0.1", "localhost", "::1"):
                log.warning("serving on %s without a token: anyone who can reach it can post replies",
                            args.host)
        _out("Press Ctrl+C to stop.")
        sys.stdout.flush()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            _out("\nStopped.")
        finally:
            server.server_close()
        return EXIT_OK
    finally:
        session.close()


# =============================================================================
# stats / mark / suppress / followups / adapters
# =============================================================================

def cmd_stats(args: argparse.Namespace) -> int:
    from .report import funnel_report, runs_report

    since = None
    if args.since:
        since = parse_date(args.since)
        if since is None:
            raise CliError(f"--since: cannot read {args.since!r} as a date (use YYYY-MM-DD)")
    session = open_session(args, need_playbook=False)
    try:
        _out(funnel_report(session.store, session.scope, since).rstrip("\n"))
        _out("")
        _out(runs_report(session.store, session.scope, limit=args.runs).rstrip("\n"))
        due = session.store.due_followups(session.ctx.today, session.scope)
        if due:
            _out("")
            _out(f"{len(due)} follow-up(s) due today - see: leadgen followups{_pb_hint(session)}")
        return EXIT_OK
    finally:
        session.close()


def cmd_mark(args: argparse.Namespace) -> int:
    session = open_session(args, need_playbook=False)
    try:
        store = session.store
        if args.lead:
            lead = store.get_lead(args.lead)
            what = f"lead {args.lead}"
        else:
            lead = store.find_lead_by_email(args.email, session.scope)
            what = f"lead with email {args.email}" + (f" in playbook {session.scope}" if session.scope else "")
        if lead is None:
            raise CliError(f"no {what} found in {store.path}", EXIT_PROBLEM)
        before = lead.stage
        changed = store.set_stage(lead.id, args.stage, note=args.note or "manual", force=args.force)
        who = (lead.contact.full_name or lead.contact.email) if lead.contact else "-"
        desc = f"{lead.company.name} / {who} ({lead.id})"
        if changed:
            _out(f"{desc}: {before} -> {args.stage}")
        elif before == args.stage:
            _out(f"{desc}: already '{before}' (event recorded)")
        else:
            _out(f"{desc}: stays '{before}' - stages only move forward; add --force to move it back "
                 f"to '{args.stage}'")
        return EXIT_OK
    finally:
        session.close()


def _read_values_file(path: Path) -> List[str]:
    """Values from a TXT (one per line, # comments) or CSV (column email/domain/value, else first)."""
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() in (".csv", ".tsv"):
        delim = "\t" if path.suffix.lower() == ".tsv" else ","
        rows = list(csv.reader(text.splitlines(), delimiter=delim))
        if not rows:
            return []
        header = [h.strip().lower() for h in rows[0]]
        col = next((header.index(h) for h in ("email", "domain", "value", "email_address", "website")
                    if h in header), None)
        body = rows[1:] if col is not None else rows
        col = col or 0
        return [r[col].strip() for r in body if len(r) > col and r[col].strip()]
    out = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def cmd_suppress(args: argparse.Namespace) -> int:
    session = open_session(args, need_playbook=False)
    try:
        store = session.store
        if args.action == "list":
            rows = store.list_suppressed()
            if not rows:
                _out(f"The suppression list in {store.path} is empty.")
                return EXIT_OK
            _out(f"{len(rows)} suppressed value(s) in {store.path}:")
            _out("")
            _out(format_table(["value", "kind", "reason", "added"],
                              [[r["value"], r["kind"], r.get("reason") or "", r.get("added_at") or ""]
                               for r in rows], widths={"value": 50, "reason": 40}))
            return EXIT_OK
        values: List[str] = [args.value] if args.value else []
        if args.file:
            fp = Path(args.file)
            if not fp.is_file():
                raise FileNotFoundError(2, "file not found", str(fp))
            values += _read_values_file(fp)
        if not values:
            raise CliError(f"suppress {args.action}: give a VALUE or --file")
        done = 0
        for v in values:
            if args.action == "add":
                kind = args.kind or ("email" if "@" in v else "domain")
                store.suppress(v, kind, args.reason or "manual")
                done += 1
            else:
                done += store.unsuppress(v)
        verb = "Added" if args.action == "add" else "Removed"
        _out(f"{verb} {done} value(s)" + (f" (of {len(values)} given)" if done != len(values) else "")
             + f" {'to' if args.action == 'add' else 'from'} the suppression list in {store.path}.")
        return EXIT_OK
    finally:
        session.close()


def cmd_followups(args: argparse.Namespace) -> int:
    session = open_session(args, need_playbook=False)
    try:
        store = session.store
        if args.done is not None:
            row = store.conn.execute("SELECT id, done FROM followups WHERE id=?", (args.done,)).fetchone()
            if not row:
                raise CliError(f"follow-up #{args.done} not found in {store.path}", EXIT_PROBLEM)
            store.complete_followup(args.done)
            _out(f"Follow-up #{args.done} marked done.")
            return EXIT_OK
        until = session.ctx.today + timedelta(days=max(0, args.days))
        due = store.due_followups(until, session.scope)
        span = "today" if args.days <= 0 else f"in the next {args.days} day(s)"
        if not due:
            _out(f"No follow-ups due {span}.")
            return EXIT_OK
        rows = []
        for f in due:
            ld = store.get_lead(f["lead_id"]) if f.get("lead_id") else None
            rows.append([f["id"], f["due"], f.get("email") or "-", ld.company.name if ld else "-",
                         f.get("reason") or ""])
        _out(f"{len(due)} follow-up(s) due {span}:")
        _out("")
        _out(format_table(["id", "due", "email", "company", "reason"], rows,
                          widths={"email": 40, "company": 28}, right=("id",)))
        _out("")
        _out(f"Done with one? leadgen followups{_pb_hint(session)} --done <id>")
        return EXIT_OK
    finally:
        session.close()


def cmd_adapters(args: argparse.Namespace) -> int:
    _out("Adapter types by kind (use them as 'type:' in a playbook):")
    for kind in registry.KINDS:
        _out("")
        _out(f"{kind}:")
        rows = []
        for name in registry.available(kind):
            try:
                cls = registry.resolve(kind, name)
                env = getattr(cls, "env_key", "") or ""
                where = "offline" if getattr(cls, "offline", False) else "network"
                doc = (cls.__doc__ or "").strip().splitlines()[0] if cls.__doc__ else ""
            except Exception as e:  # noqa: BLE001 - a broken optional adapter must not hide the rest
                env, where, doc = "", "unavailable", f"cannot import: {e}"
            creds = CREDENTIALS.get((kind, name))
            if creds:
                env = ", ".join(e for _, e in creds)
            rows.append(["  " + name, where, env or "-", doc])
        _out(format_table(["type", "runs", "credential", "description"], rows,
                          widths={"description": 70}))
    return EXIT_OK


# =============================================================================
# parser + main
# =============================================================================

class _EnvFileAction(argparse.Action):
    """Remembers that --env-file was given explicitly (a missing file is then an error)."""

    def __call__(self, parser: Any, namespace: Any, values: Any, option_string: Any = None) -> None:
        setattr(namespace, self.dest, values)
        setattr(namespace, "env_file_explicit", True)


def _add_global_options(p: argparse.ArgumentParser, suppress: bool) -> None:
    def d(value: Any) -> Any:
        return argparse.SUPPRESS if suppress else value

    g = p.add_argument_group("global options")
    g.add_argument("-p", "--playbook", metavar="PATH", default=d(None),
                   help="playbook YAML file (e.g. playbooks/demo-offline.yaml)")
    g.add_argument("--db", metavar="PATH", default=d(None),
                   help="SQLite database file (overrides the playbook's storage.path)")
    g.add_argument("-v", "--verbose", action="count", default=d(0),
                   help="more output: -v info, -vv debug (and full tracebacks)")
    g.add_argument("--env-file", metavar="PATH", default=d(DEFAULT_ENV_FILE), action=_EnvFileAction,
                   help="KEY=VALUE file with API keys (default: .env; existing env vars win)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="leadgen",
        description="Signal-led lead generation: find companies with a reason to buy now, "
                    "find the decision-maker, verify the email, score, write and export.",
        epilog="Start here: leadgen run -p playbooks/demo-offline.yaml   (no API keys needed)")
    parser.add_argument("--version", action="version", version=f"leadgen {__version__}")
    _add_global_options(parser, suppress=False)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    parent = argparse.ArgumentParser(add_help=False)
    _add_global_options(parent, suppress=True)

    def add(name: str, func: Callable[[argparse.Namespace], int], help_text: str) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, parents=[parent], help=help_text, description=help_text)
        sp.set_defaults(func=func)
        return sp

    sp = add("init", cmd_init, "create a new playbook from a template")
    sp.add_argument("name", help="playbook name (letters, digits, - _ .)")
    sp.add_argument("--template", choices=TEMPLATES, default="generic", help="template to copy (default: generic)")
    sp.add_argument("--dir", default="playbooks", help="where to write it (default: playbooks)")

    sp = add("validate", cmd_validate, "check a playbook: adapters, config and API keys")
    sp.add_argument("--dry-run", action="store_true",
                    help="check for a --dry-run (keys of network adapters become optional)")

    sp = add("run", cmd_run, "run the pipeline once: find, filter, enrich, verify, score, write, export")
    sp.add_argument("--out", metavar="DIR", help="output folder (default: output/<playbook>/); "
                                                 "each run gets its own sub-folder")
    sp.add_argument("--limit", type=int, metavar="N", help="only enrich/score the top N companies (cheap test)")
    sp.add_argument("--dry-run", action="store_true",
                    help="skip everything that uses the network (no paid API calls, nothing sent)")

    sp = add("demo", cmd_demo, "write the 'live opportunities' report (Markdown + HTML) for a run")
    sp.add_argument("--run", default="latest", metavar="RUN_ID", help="run id or 'latest' (default)")
    sp.add_argument("--top", type=int, default=5, help="opportunities to show (default 5; 0 = all)")
    sp.add_argument("--prospect", metavar="NAME", help="who the report is for (used in the title + file name)")
    sp.add_argument("--no-mask", action="store_true", help="show email addresses in full")
    sp.add_argument("--out", metavar="DIR", help="output folder (default: the run's folder)")

    sp = add("leads", cmd_leads, "show the leads of a run")
    sp.add_argument("--run", default="latest", metavar="RUN_ID", help="run id, 'latest' (default) or 'all'")
    sp.add_argument("--tier", choices=(Tier.HOT, Tier.NORMAL, Tier.SKIP), help="only this tier")
    sp.add_argument("--limit", type=int, default=20, help="rows to show (default 20; 0 = all)")

    sp = add("replies", cmd_replies, "classify a replies CSV and act on each reply")
    sp.add_argument("--file", required=True, metavar="CSV", help="replies export (from_email, subject, body, ...)")
    sp.add_argument("--out", metavar="DIR", help="where replies_classified.csv goes (default: output/<playbook>/)")

    sp = add("serve", cmd_serve, "run the reply webhook server (Instantly / Smartlead / generic)")
    sp.add_argument("--host", default="127.0.0.1", help="interface to bind (default 127.0.0.1)")
    sp.add_argument("--port", type=int, default=8787, help="port (default 8787)")
    sp.add_argument("--token", help=f"shared secret required on every webhook (default: ${WEBHOOK_TOKEN_ENV})")

    sp = add("stats", cmd_stats, "funnel numbers and recent runs")
    sp.add_argument("--since", metavar="YYYY-MM-DD", help="only leads created since this date")
    sp.add_argument("--runs", type=int, default=10, help="recent runs to list (default 10)")

    sp = add("mark", cmd_mark, "move a lead to a stage by hand (booked, won, lost, ...)")
    who = sp.add_mutually_exclusive_group(required=True)
    who.add_argument("--email", help="the lead's contact email")
    who.add_argument("--lead", metavar="ID", help="the lead id (see opportunities.csv / leadgen leads)")
    sp.add_argument("--stage", required=True, choices=Stage.ALL, help="new stage")
    sp.add_argument("--note", help="free-text note stored with the change")
    sp.add_argument("--force", action="store_true", help="allow moving a lead backwards")

    sp = add("suppress", cmd_suppress, "manage the do-not-contact list")
    sp.add_argument("action", choices=("add", "remove", "list"))
    sp.add_argument("value", nargs="?", help="an email address or a domain")
    sp.add_argument("--kind", choices=("email", "domain"), help="default: email if it contains @, else domain")
    sp.add_argument("--reason", help="why (shown in the list)")
    sp.add_argument("--file", metavar="CSV/TXT", help="many values: one per line, or a CSV column email/domain")

    sp = add("followups", cmd_followups, "list follow-ups that are due (from timing / out-of-office replies)")
    sp.add_argument("--done", type=int, metavar="ID", help="mark this follow-up as done")
    sp.add_argument("--days", type=int, default=0, help="also show those due in the next N days")

    add("adapters", cmd_adapters, "list every adapter type per kind")
    return parser


def _friendly(e: BaseException) -> str:
    if isinstance(e, FileNotFoundError):
        name = e.filename or ""
        what = e.strerror or "file not found"
        return f"{what}: {name}" if name else str(e)
    if isinstance(e, registry.UnknownAdapterError):
        return str(e.args[0] if e.args else e)
    return str(e) or type(e).__name__


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point for ``leadgen`` / ``python -m leadgen``; returns the exit code."""
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as e:  # --help / --version / usage errors
        return int(e.code or 0) if not isinstance(e.code, str) else EXIT_USAGE
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_USAGE
    verbose = int(getattr(args, "verbose", 0) or 0)
    restore_logging = setup_logging(verbose)
    try:
        return _dispatch(args, verbose)
    finally:
        restore_logging()


def _dispatch(args: argparse.Namespace, verbose: int) -> int:
    """Run the command; turn known problems into one friendly line + exit code."""
    try:
        load_plugins(build_env(args))
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        sys.stderr.write("interrupted\n")
        return 130
    except BrokenPipeError:
        # output piped into e.g. `head`, which stopped reading: not an error. Point stdout
        # at devnull so the interpreter's final flush doesn't raise again.
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except (OSError, ValueError, AttributeError):
            pass
        return EXIT_OK
    except (CliError, PlaybookError, MissingCredentialError, FileNotFoundError,
            registry.UnknownAdapterError) as e:
        if verbose:
            traceback.print_exc()
        sys.stderr.write(f"error: {_friendly(e)}\n")
        return e.code if isinstance(e, CliError) else EXIT_USAGE
    except Exception as e:  # noqa: BLE001 - last resort: one line unless -v
        if verbose:
            traceback.print_exc()
            return EXIT_PROBLEM
        sys.stderr.write(f"error: unexpected {type(e).__name__}: {e} (run with -v for details)\n")
        return EXIT_PROBLEM


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
