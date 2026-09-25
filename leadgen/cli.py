"""Command-line interface: ``leadgen <command> [options]`` (also ``python -m leadgen``).

Modes
-----
Every playbook runs in one of two modes (``mode:`` in the playbook, see
``leadgen/modes.py``):

* ``delivery`` (the default): the business sells lead files. A run stops after
  scoring and writes lead files; ``leadgen deliver`` makes a client's weekly
  "Hiring Signal Report". Nothing is ever written to or sent to anyone.
* ``outbound``: the outreach features - email copy, hand-over to sending
  tools, replies, follow-ups and the reply webhook server.

Commands
--------
init NAME       Create ``playbooks/NAME.yaml`` from a template (never overwrites).
validate        Load a playbook, build every adapter it uses and report unknown
                types, config mistakes and missing API keys as a checklist. Shows
                the mode; in delivery mode outbound-only settings (AI writer,
                hand-over exporters) are warnings, not failures.
run             One full pipeline run. Delivery mode: find -> filter -> enrich ->
                verify -> score -> lead files. Outbound mode adds write + hand-over.
                ``--dry-run`` skips everything that uses the network (no paid
                calls, nothing sent); ``--budget N`` caps paid lookups. The API
                usage (+ estimated cost) is printed after every run.
deliver         One client's weekly Hiring Signal Report (``--client NAME`` =
                ``clients/NAME.yaml``): client-ready CSV / Excel / HTML files, the
                internal QA files, then the QA summary. ``--dry-run`` = preview
                (``...-PREVIEW`` files, nothing recorded as delivered).
clients         ``clients [list]``: every client with its weekly volume and delivery
                history. ``clients new NAME``: a new client file from the template.
doctor          Test every API key a playbook (``-p``) or client (``--client``) uses:
                one free account / credits call per key - never a paid lookup.
demo            Write the "live opportunities" one-pager (Markdown + HTML) for a run.
leads           Show the leads of a run as a table.
replies         (outbound mode) Classify a replies CSV and act on every reply
                (stages, suppression, follow-ups, alerts); writes ``replies_classified.csv``.
serve           (outbound mode) Run the webhook receiver for Instantly / Smartlead
                reply events.
stats           Funnel numbers + recent runs.
mark            Move a lead to a stage by hand (booked, won, lost, ...).
suppress        Manage the do-not-list (add / remove / list): emails, domains,
                company names and LinkedIn profiles. Global by default; with
                ``--client NAME`` that client's own list (never delivered to them).
followups       (outbound mode) Show follow-ups that are due; ``--done ID`` ticks one off.
adapters        List every adapter type the engine knows, per kind: offline /
                network / network (paid), its credential, and notes (use-at-own-risk
                scrapers, outbound-only adapters).

``replies``, ``serve`` and ``followups`` refuse to run (exit 2, nothing opened)
unless ``-p`` names an outbound-mode playbook - without ``-p`` the default mode,
delivery, applies.

Global options (accepted before or after the command)
-----------------------------------------------------
-p / --playbook PATH   The playbook YAML (required by run, validate, demo, replies,
                       serve; optional elsewhere, where it scopes the output;
                       ignored by deliver, which uses the client file's playbook).
--db PATH              SQLite file; overrides the playbook's ``storage.path``
                       (default without a playbook: ``data/leadgen.db``; with
                       ``--client``: the storage path of the client's playbook).
-v / --verbose         More logging (-v info, -vv debug) and full tracebacks.
--env-file PATH        ``KEY=VALUE`` file loaded before anything else (default
                       ``.env`` in the current directory, skipped when missing).
                       Variables already set in the environment always win.

Command options read by the delivery commands: ``--client NAME`` and
``--clients-dir DIR`` (default ``clients``), ``--budget N`` (paid-lookup cap for
the run; 0 = no cap; overrides ``usage.max_paid_lookups`` and the client file's
``budget.max_paid_lookups``), ``--out DIR`` (deliver: the output folder,
``{client}`` / ``{date}`` placeholders allowed). The playbook keys the CLI itself
reads: ``mode``, ``name``, ``storage.path``, ``sources``, ``notify`` (to avoid
printing the run summary twice), ``writer``, ``outbound.exporters``,
``enrichment``, ``buyers.titles``, ``offer.sender_name`` / ``delivery.sender_name``
and ``replies.classifier`` (validate).

Plugins: ``LEADGEN_PLUGINS=my_pkg.adapters,other_module`` (environment or .env)
imports those modules before any command runs, so they can add their own
adapter types with ``leadgen.registry.register(kind, type, "module:Class")`` -
with ``paid=True`` for an adapter that calls a paid API, so its requests are
paid lookups capped by ``--budget`` (without it they are counted, never capped).
The working directory is importable for this.

Exit codes: 0 success, 1 the command ran but found a problem (validation
failed, every source failed, no leads delivered, a key failed the doctor,
nothing to show), 2 usage / configuration error (bad playbook or client file,
missing file or credential, an outbound-only command in delivery mode) -
reported as one friendly line on stderr, with the traceback only under ``-v``.
"""
from __future__ import annotations

import argparse
import copy
import csv
import dataclasses
import difflib
import importlib
import logging
import os
import re
import shlex
import sqlite3
import sys
import traceback
from dataclasses import dataclass
from datetime import date, timedelta
from email.utils import parseaddr
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import yaml

from . import __version__, registry
from .context import Context, MissingCredentialError
from .delivery.client import (DEFAULT_CLIENTS_DIR, Client, ClientError, is_handover_exporter,
                              list_clients)
from .http import HttpClient
from .models import Lead, ReplyCategory, Stage, Tier
from .modes import DELIVERY, OUTBOUND, is_outbound, mode_of, outbound_only_message
from .playbook import DEFAULTS, Playbook, PlaybookError, from_dict, load_playbook
from .store import SUPPRESSION_KINDS, Store, normalize_suppression
from .usage import UsageMeter
from .utils import is_valid_email, normalize_domain, parse_date

EXIT_OK = 0
EXIT_PROBLEM = 1
EXIT_USAGE = 2

TEMPLATES = ("generic", "recruitment", "saas-funding", "local-business", "agency-outreach")
DEFAULT_ENV_FILE = ".env"
DEFAULT_DB = str(DEFAULTS["storage"]["path"])
WEBHOOK_TOKEN_ENV = "LEADGEN_WEBHOOK_TOKEN"
PLUGINS_ENV = "LEADGEN_PLUGINS"
OUTBOUND_EXAMPLE = "playbooks/demo-offline.yaml"   # an outbound-mode playbook shipped with the repo

_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

MODE_TEXT = {
    DELIVERY: "delivery - lead files only: no email copy is written, nothing is sent to anyone",
    OUTBOUND: "outbound - writes email sequences and hands leads to sending tools (replies, follow-ups)",
}

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


def make_http() -> Any:
    """The HTTP client a command uses (one per command).

    Every command gets its client from here, so tests can replace this function
    (``monkeypatch.setattr(cli, "make_http", lambda: FakeHttp())``) and no test
    ever reaches the real network."""
    return HttpClient()


def close_http(http: Any) -> None:
    """Close the HTTP client's connection pool (best effort)."""
    closer = getattr(getattr(http, "session", None), "close", None)
    try:
        if callable(closer):
            closer()
    except Exception:  # noqa: BLE001 - best-effort cleanup
        pass


def open_store(db: str) -> Store:
    """Open the SQLite database ``db``; a problem becomes one friendly line (exit 2)."""
    try:
        return Store(os.path.expanduser(str(db)))
    except (sqlite3.Error, OSError) as e:
        raise CliError(f"cannot open the database {db}: {e}") from e


def check_budget(value: Optional[int]) -> Optional[int]:
    """``--budget N``: None (not given) or a whole number >= 0 (0 = no cap)."""
    if value is None:
        return None
    if value < 0:
        raise CliError(f"--budget must be 0 or more (got {value}): it is the most paid lookups this run may make "
                       f"(0 = no cap). For no paid calls at all, use --dry-run.")
    return value


def open_session(args: argparse.Namespace, *, need_playbook: bool, dry_run: bool = False,
                 budget: Optional[int] = None) -> Session:
    """Load the playbook (if any), open the store and build the ``Context``.

    ``budget`` (``--budget N``) overrides the playbook's ``usage.max_paid_lookups``."""
    env = build_env(args)
    pb_path = getattr(args, "playbook", None)
    playbook: Optional[Playbook] = None
    if pb_path:
        playbook = load_playbook(pb_path, env=env)
    elif need_playbook:
        raise CliError(f"'{args.command}' needs a playbook: add -p playbooks/<name>.yaml "
                       f"(try: leadgen {args.command} -p playbooks/demo-offline.yaml)")
    db = getattr(args, "db", None) or (str(playbook.db_path) if playbook else DEFAULT_DB)
    store = open_store(db)
    pb = playbook or from_dict({"name": "leadgen"}, env=env)
    ctx = Context(playbook=pb, http=make_http(), store=store, env=env, today=date.today(), log=log,
                  dry_run=dry_run, usage=UsageMeter.from_playbook(pb, budget))
    return Session(ctx=ctx, store=store, playbook=playbook, playbook_path=pb_path)


def outbound_gate(args: argparse.Namespace, feature: str) -> Optional[int]:
    """Refuse an outbound-only command unless ``-p`` names an outbound-mode playbook.

    Returns None when the command may run, else prints the reason to stderr and
    returns exit code 2 - before the database, the network or any file is touched.
    Without ``-p`` the default mode (delivery) applies."""
    pb_path = getattr(args, "playbook", None)
    name = ""
    if pb_path:
        playbook = load_playbook(pb_path, env=build_env(args))
        if is_outbound(playbook):
            return None
        name = playbook.name
    sys.stderr.write(f"error: {outbound_only_message(feature, name)}\n")
    if not pb_path:
        sys.stderr.write(f"No playbook was given (-p), so the default mode (delivery) applies. Name an "
                         f"outbound-mode playbook, e.g. leadgen {args.command} -p {OUTBOUND_EXAMPLE}\n")
    return EXIT_USAGE


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


def _enabled(cfg: Any) -> bool:
    return isinstance(cfg, dict) and cfg.get("enabled") is not False


def risk_checks(kind: str, cfg: Any) -> List[Check]:
    """A warning for an enabled adapter that scrapes a site whose terms forbid it
    (``registry.risk_note`` starting with 'use at own risk')."""
    if not _enabled(cfg):
        return []
    note = registry.risk_note(kind, str(cfg.get("type") or ""), cfg)
    if note.startswith("use at own risk"):
        return [Check("warn", _describe(kind, cfg), note)]
    return []


def _llm_config(w: Dict[str, Any]) -> Dict[str, Any]:
    """The writer's LLM entry, built the same way as ``llm.build_llm``."""
    llm_cfg = dict(w.get("llm") or {})
    llm_cfg.update({"type": w.get("provider"), "model": w.get("model"), "base_url": w.get("base_url"),
                    "temperature": w.get("temperature")})
    if w.get("api_key_env"):
        llm_cfg["api_key_env"] = w["api_key_env"]
    return llm_cfg


def _llm_label(w: Dict[str, Any]) -> str:
    return f"{w.get('provider')}{', ' + str(w.get('model')) if w.get('model') else ''}"


def _writer_checks_delivery(ctx: Context) -> List[Check]:
    """Delivery mode: the writer never runs; its AI model is only used for AI opening lines."""
    w = ctx.playbook.writer
    provider = str(w.get("provider") or "")
    checks: List[Check] = []
    if w.get("type") == "ai":
        detail = "ignored in delivery mode - no email copy is written"
        if provider:
            detail += ("; writer.provider / writer.model are still used for AI opening lines "
                       "(clients with opening_line.ai: true)")
        checks.append(Check("warn", f"writer ai ({_llm_label(w) if provider else 'no provider'})", detail))
    else:
        checks.append(Check("skip", "writer", "not used in delivery mode - no email copy is written"))
    if provider:
        c = check_adapter("llm", _llm_config(w), ctx)
        c.label = f"AI opening lines ({_llm_label(w)})"
        if c.status == "fail":
            c.status = "warn"
            c.detail += (" - only matters for clients with opening_line.ai: true (until it is fixed they "
                         "get the free template line)")
        elif c.status == "ok":
            c.detail += " - used only for clients with opening_line.ai: true"
        checks.append(c)
    return checks


def _writer_checks_outbound(ctx: Context) -> List[Check]:
    w = ctx.playbook.writer
    if w.get("type") == "ai":
        c = check_adapter("llm", _llm_config(w), ctx)
        c.label = f"writer ai ({_llm_label(w)})"
        if c.status == "fail" and w.get("fallback_to_template", True):
            c.detail += " - the template writer would be used instead"
        return [c]
    return [check_adapter("writer", dict(w, type="template"), ctx)]


def validate_playbook(ctx: Context) -> List[Check]:
    """Checklist for every adapter the playbook uses, plus a few sanity checks.

    In delivery mode the outbound-only settings - the email writer and hand-over
    exporters (Instantly, Smartlead, upload CSVs, webhooks) - are never used, so
    they are reported as warnings ("ignored in delivery mode"), never failures.
    Adapters that scrape sites against their terms get a 'use at own risk' warning."""
    pb = ctx.playbook
    outbound = is_outbound(pb)
    checks: List[Check] = []
    active_sources = [s for s in pb.sources if not (isinstance(s, dict) and s.get("enabled") is False)]
    if not active_sources:
        checks.append(Check("fail", "sources", "no sources configured - nothing to find (add one under 'sources:')"))
    for cfg in pb.sources:
        checks.append(check_adapter("source", cfg, ctx))
        checks += risk_checks("source", cfg)
    finders = pb.enrichment.get("finders") or []
    if not finders:
        checks.append(Check("warn", "finders", "no contact finders - only contacts supplied by sources are used"))
    for cfg in finders:
        checks.append(check_adapter("finder", cfg, ctx))
        checks += risk_checks("finder", cfg)
    ver = pb.enrichment.get("verifier")
    if ver:
        checks.append(check_adapter("verifier", ver, ctx))
    elif outbound:
        checks.append(Check("warn", "verifier", "none configured - emails are not checked before sending"))
    else:
        checks.append(Check("warn", "verifier", "none configured - every email is delivered as not verified"))
    checks += _writer_checks_outbound(ctx) if outbound else _writer_checks_delivery(ctx)
    exporters = pb.outbound.get("exporters") or []
    if not exporters:
        checks.append(Check("warn", "exporters", "none configured - results only land in the database"
                            + ("" if outbound else " (the client files of `leadgen deliver` are not affected)")))
    for cfg in exporters:
        if not outbound and _enabled(cfg) and is_handover_exporter(str(cfg.get("type") or "")):
            checks.append(Check("warn", _describe("exporter", cfg),
                                "hands leads to a sending tool - ignored in delivery mode (lead files only); "
                                "remove it, or set 'mode: outbound' to use it"))
            continue
        checks.append(check_adapter("exporter", cfg, ctx))
    for cfg in pb.notify.get("channels") or []:
        checks.append(check_adapter("notifier", cfg, ctx))
    w = pb.writer
    if outbound and pb.replies.get("classifier") == "ai" and w.get("type") != "ai" and not w.get("provider"):
        checks.append(Check("warn", "replies", "classifier 'ai' needs writer.provider - the rules are used instead"))
    if not (pb.buyers.get("titles") or []):
        checks.append(Check("warn", "buyers", "buyers.titles is empty - any contact counts as a decision-maker"))
    if outbound and not pb.offer.get("sender_name"):
        checks.append(Check("warn", "offer", "offer.sender_name is empty - emails would go out unsigned"))
    if not outbound and not str((pb.delivery or {}).get("sender_name") or "").strip():
        checks.append(Check("warn", "delivery", "delivery.sender_name is empty - the report's footer won't "
                                                "say who it is from"))
    return checks


_MARK = {"ok": "[ok]  ", "warn": "[warn]", "fail": "[FAIL]", "skip": "[skip]"}


def cmd_validate(args: argparse.Namespace) -> int:
    session = open_session(args, need_playbook=True, dry_run=args.dry_run)
    try:
        pb = session.ctx.playbook
        _out(f"Playbook: {pb.name}  ({session.playbook_path})")
        _out(f"Mode: {MODE_TEXT.get(mode_of(pb), mode_of(pb))}")
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
        if not is_outbound(pb):
            _out("For a client's weekly report: leadgen deliver --client <name> --dry-run   "
                 "(clients/<name>.yaml names this playbook in its 'playbook:' line)")
        return EXIT_OK
    finally:
        session.close()


# =============================================================================
# run / demo / leads
# =============================================================================

def _console_prints(channel: Any, event: str) -> bool:
    """True for an enabled console channel that prints ``event`` to stdout."""
    if not isinstance(channel, dict) or channel.get("type") != "console" or channel.get("enabled") is False:
        return False
    if str(channel.get("stream") or "stdout").lower() != "stdout":
        return False
    events = channel.get("events")
    return not events or event in events


def _console_shows(pb: Playbook, event: str) -> bool:
    """True when a console notifier will already print ``event`` to stdout."""
    if event not in (pb.notify.get("on") or []):
        return False
    return any(_console_prints(ch, event) for ch in pb.notify.get("channels") or [])


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
    # The pipeline reports at most one "source <label>: ..." error per source, and
    # unlabeled sources of one type share a label: count errors per label, capped at
    # the number of sources carrying it (one failing csv of two is not "both failed").
    per_label: Dict[str, int] = {}
    for cfg in active:
        label = str(cfg.get("label") or cfg.get("type"))
        per_label[label] = per_label.get(label, 0) + 1
    failed = 0
    for label, n in per_label.items():
        failed += min(n, sum(1 for e in errors if e.startswith(f"source {label}:")))
    return len(active), failed


def cmd_run(args: argparse.Namespace) -> int:
    from .pipeline import Pipeline

    if args.limit is not None and args.limit < 1:
        raise CliError("--limit must be a positive number")
    budget = check_budget(args.budget)
    session = open_session(args, need_playbook=True, dry_run=args.dry_run, budget=budget)
    try:
        ctx = session.ctx
        pb = ctx.playbook
        mode = " (dry run: nothing that uses the network runs, nothing is sent)" if args.dry_run else ""
        cap = f", at most {budget} paid lookup(s)" if budget else ""
        _out(f"Running playbook '{pb.name}' ({mode_of(pb)} mode{cap}){mode} ...")
        result = Pipeline(ctx, out_dir=Path(args.out) if args.out else None, limit=args.limit).run()
        if not _console_shows(pb, "run_summary"):
            # RunResult.summary() ends with the API usage lines (paid lookups, estimated cost);
            # when a console notifier is configured it has just printed this same summary
            _out(result.summary())
        _out("")
        _out(f"Files in {result.out_dir}:")
        rehearsal = "dry-run rehearsal only - nobody was recorded as handed over: do NOT import it"
        for name, what in (("opportunities.csv", "every lead with score, reasons and emails (review / Google Sheets)"),
                           ("instantly_upload.csv", "ready to import into Instantly"),
                           ("smartlead_upload.csv", "ready to import into Smartlead"),
                           ("instantly_upload.dry-run.csv", rehearsal),
                           ("smartlead_upload.dry-run.csv", rehearsal),
                           ("leads.json", "everything, for other tools"),
                           ("rejected.csv", "companies filtered out, with the reason"),
                           ("summary.json", "run counts + top leads")):
            if (result.out_dir / name).exists():
                _out(f"  {name:<28} {what}")
        if args.dry_run and any(result.out_dir.glob("*.dry-run.*")):
            _out("  (run without --dry-run for the real upload files: only a real run records who was "
                 "handed over, so nobody gets the sequence twice)")
        _out("")
        _out("Next:")
        _out(f"  leadgen leads{_pb_hint(session)}      # the leads of this run")
        _out(f"  leadgen demo{_pb_hint(session)}       # one-page 'live opportunities' report")
        if not is_outbound(pb):
            _out("  leadgen deliver --client <name>   # a client's weekly report (clients/<name>.yaml)")
        if args.dry_run and not result.counts.get("sourced"):
            _out("")
            _out("Note: nothing was found because every source uses the network and --dry-run skips "
                 "them. Add a csv/json source to rehearse offline, or run without --dry-run.")
        active, failed = _failed_sources(pb, result.errors, args.dry_run)
        if active and failed >= active and not result.counts.get("sourced"):
            sys.stderr.write(f"error: every source failed ({failed} of {active}) - see the errors above\n")
            return EXIT_PROBLEM
        configured = [s for s in pb.sources if isinstance(s, dict) and s.get("enabled") is not False]
        if not configured:
            sys.stderr.write("warning: the playbook has no sources configured - nothing was found\n")
        return EXIT_OK
    finally:
        session.close()


def honest_demo_leads(leads: Sequence[Lead]) -> List[Lead]:
    """The run's leads as the demo one-pager shows them: an address built from a name
    pattern (``rows.is_guessed``) carries the label the client files give it,
    ``guessed-unverified``, as its status - never a checker's ``valid``. So the sample
    report shown to prospects never calls a guess "verified" nor counts it as "with a
    verified email". Shallow copies: the stored leads are not changed."""
    from .delivery.rows import GUESSED, is_guessed

    out: List[Lead] = []
    for ld in leads:
        ct = ld.contact
        if ct is not None and is_guessed(ct) and ct.email_status != GUESSED:
            shown = copy.copy(ct)
            shown.email_status = GUESSED
            ld = copy.copy(ld)
            ld.contact = shown
        out.append(ld)
    return out


def cmd_demo(args: argparse.Namespace) -> int:
    from .report import write_demo

    if args.top < 0:
        raise CliError("--top must be 0 (all) or a positive number")
    session = open_session(args, need_playbook=True)
    try:
        run_id = _resolve_run(session, args.run)
        leads = honest_demo_leads(session.store.leads_for_run(run_id))
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
    return [ld.id, ld.score, ld.tier, ld.stage, ld.company.name, who or "-", email, sig.title if sig else "-"]


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
        _out(format_table(["id", "score", "tier", "stage", "company", "contact", "email", "top signal"],
                          [_lead_row(ld) for ld in leads],
                          widths={"company": 28, "contact": 38, "email": 46, "top signal": 40},
                          right=("score",)))
        return EXIT_OK
    finally:
        session.close()


# =============================================================================
# deliver / clients / doctor  (the lead-delivery business)
# =============================================================================

DELIVERY_FILE_WHAT = {
    "csv": "spreadsheet - opens in Excel / Google Sheets, imports into any CRM",
    "xlsx": "Excel workbook - 'Leads' sheet + 'About' sheet (counts, email-status legend)",
    "html": "one-page summary - open in a browser, or print to PDF",
}


def client_arg(raw: Any, clients_dir: str) -> str:
    """``--client`` as typed, with a bare name spelled as its file is stored: on a
    case-insensitive disk (macOS, Windows) ``--client ACME`` opens ``clients/acme.yaml``,
    and the client is ``acme`` - its ledger and do-not-list are kept under the file's own
    name, so another spelling must never start an empty history (and re-deliver everything)."""
    value = str(raw or "").strip()
    p = Path(value)
    if not value or p.suffix.lower() in (".yaml", ".yml") or len(p.parts) != 1:
        return value
    known = list_clients(clients_dir)
    if value in known:
        return value
    same = [k for k in known if k.casefold() == value.casefold()]
    folder = Path(clients_dir)
    if len(same) == 1 and any((folder / f"{value}{ext}").is_file() for ext in (".yaml", ".yml")):
        return same[0]
    return value


def load_client_and_playbook(args: argparse.Namespace, env: Dict[str, str]) -> Tuple[Client, Playbook]:
    """``--client NAME`` (in ``--clients-dir``) and the playbook built for it. Problems
    in the client file or its base playbook become one friendly error (exit 2)."""
    from .delivery.client import client_playbook, load_client

    try:
        client = load_client(client_arg(args.client, args.clients_dir), args.clients_dir)
        return client, client_playbook(client, env=env)
    except ClientError as e:
        raise CliError(str(e)) from e


def without_console_summary(client: Client, pb: Playbook) -> Client:
    """A copy of ``client`` whose run leaves out the console channels that would print
    the pipeline's run summary: ``leadgen deliver`` prints the delivery's QA summary
    instead (the same counts, plus what was actually delivered - the pipeline's own
    "email verified" count would contradict the honest labels in the files).
    Slack / webhook channels are kept; the client file on disk is not changed."""
    if not _console_shows(pb, "run_summary"):
        return client
    channels = [ch for ch in pb.notify.get("channels") or [] if not _console_prints(ch, "run_summary")]
    overrides = copy.deepcopy(dict(client.overrides or {}))
    notify = dict(overrides.get("notify") or {})
    notify["channels"] = channels
    overrides["notify"] = notify
    return dataclasses.replace(client, overrides=overrides)


def _recipient(client: Client) -> str:
    """Who gets the files: 'Sam Lee <sam@acme.example> at Acme Staffing', or the client's name."""
    contact = client.contact if isinstance(client.contact, dict) else {}
    name = str(contact.get("name") or "").strip()
    email = str(contact.get("email") or "").strip()
    who = f"{name} <{email}>" if name and email else (name or email)
    return f"{who} at {client.display_name}" if who else client.display_name


def _client_options(args: argparse.Namespace) -> str:
    """The options to repeat in a suggested follow-up command (clients dir, db, out)."""
    parts = []
    if args.clients_dir != DEFAULT_CLIENTS_DIR:
        parts.append(f"--clients-dir {shlex.quote(str(args.clients_dir))}")
    if getattr(args, "db", None):
        parts.append(f"--db {shlex.quote(str(args.db))}")
    if getattr(args, "out", None):
        parts.append(f"--out {shlex.quote(str(args.out))}")
    return (" " + " ".join(parts)) if parts else ""


def _same_database(a: Any, b: Any) -> bool:
    """True when two database paths name the same SQLite file."""
    def norm(p: Any) -> str:
        s = os.path.expanduser(str(p))
        return s if s == ":memory:" else os.path.normcase(os.path.abspath(s))
    return norm(a) == norm(b)


def _other_files(folder: Any, files: Dict[str, Any], internal: Any) -> List[str]:
    """What a delivery folder holds besides this delivery's client files and its
    ``_internal`` folder (e.g. PREVIEW files or anything put there by hand), sorted.
    Hidden files (``.DS_Store`` ...) are ignored."""
    keep = {Path(p).name for p in files.values()}
    if internal is not None:
        keep.add(Path(internal).name)
    try:
        return sorted(p.name for p in Path(folder).iterdir() if p.name not in keep and not p.name.startswith("."))
    except OSError:
        return []


def _print_delivery(result: Any, client: Client, args: argparse.Namespace, db_note: str = "") -> None:
    """Where the files are (client-ready first, internal second), the QA summary, what next.

    ``db_note``: printed after the "Next:" line of a real delivery (``--db`` named another
    database than the client's playbook uses)."""
    folder = result.folder
    _out("")
    if result.dry_run:
        _out(f"PREVIEW files - not for the client (nothing was recorded as delivered) - in {folder}:")
    else:
        _out(f"Files for the client in {folder}:")
    width = max([len(Path(p).name) for p in result.files.values()] + [24])
    for fmt, path in result.files.items():
        _out(f"  {Path(path).name:<{width}}  {DELIVERY_FILE_WHAT.get(fmt, '')}".rstrip())
    if result.sheet_url:
        _out(f"  Google Sheet: {result.sheet_url}")
    internal = result.internal_dir
    _out("")
    _out(f"Internal files - for you, not the client - in {internal}:")
    for name, what in (("qa.txt", "this QA summary (qa.json: the same as data)"),
                       ("not_delivered.csv", "every company / lead left out, and why")):
        if internal is not None and (Path(internal) / name).exists():
            _out(f"  {name:<24} {what}")
    run_dir = Path(result.run.out_dir)
    if run_dir.exists():
        _out(f"  {run_dir.name + '/':<24} the pipeline run: rejected.csv, summary.json, opportunities.csv")
    _out("")
    qa = result.qa
    try:  # the files were listed above: don't list them twice
        qa = dataclasses.replace(qa, files={}, folder="", sheet_url="")
    except TypeError:
        pass
    for line in qa.lines():
        _out(line)
    _out("")
    if not result.delivered:
        _out(f"Nothing to send this time: no new leads for {client.display_name}. The reasons are above; every "
             f"company left out is listed in {Path(internal or folder) / 'not_delivered.csv'}.")
    elif result.dry_run:
        _out(f"Next: this was a preview - check the files, then make the real delivery: "
             f"leadgen deliver --client {client.name}{_client_options(args)}")
    else:
        internal_name = Path(internal).name if internal else "_internal"
        others = _other_files(folder, result.files, internal)
        if others:
            # e.g. a PREVIEW that could not be moved away: its rows were never recorded, so a
            # lead held back this week would reach the client twice - send only this delivery
            n = len(result.files)
            _out(f"Next: send ONLY the {n} file{'s' if n != 1 else ''} listed above to {_recipient(client)}. "
                 f"{folder} also holds {', '.join(others)}: not part of this delivery (never recorded as "
                 f"delivered) - don't send {'it' if len(others) == 1 else 'them'}, and not the {internal_name} "
                 f"folder either (that one is yours).")
        else:
            _out(f"Next: send the files in {folder} to {_recipient(client)} "
                 f"(not the {internal_name} folder - that one is yours).")
        if not (client.contact or {}).get("email"):
            where = client.path or f"clients/{client.name}.yaml"
            _out(f"  (tip: add 'contact: {{name: ..., email: ...}}' to {where} to see who gets them)")
        if db_note:
            _out(db_note)


def cmd_deliver(args: argparse.Namespace) -> int:
    from .delivery.run import deliver

    budget = check_budget(args.budget)
    env = build_env(args)
    if getattr(args, "playbook", None):
        log.warning("-p is ignored by 'deliver': the client file's 'playbook:' line names the base playbook")
    client, pb = load_client_and_playbook(args, env)
    usual_db = str(pb.db_path)
    db = getattr(args, "db", None) or usual_db
    db_note = ""
    if not _same_database(db, usual_db):
        # e.g. a "rehearsal" on a scratch database: the do-not-lists and the delivery history
        # in the client's real database were not applied, and nothing was recorded there
        db_note = (f"  Careful: this delivery used --db {db}, not {usual_db} (the database of this client's "
                   f"playbook, storage.path). Only the do-not-lists and delivery history stored in {db} were "
                   f"applied, and it was recorded there only. Send these files only if {db} is where you keep "
                   f"every delivery to {client.name} - never the files of a rehearsal.")
    store = open_store(db)
    http = make_http()
    try:
        preview = " - DRY RUN (preview: offline sources only, nothing recorded as delivered)" if args.dry_run else ""
        cap = f" - at most {budget} paid lookup(s)" if budget else ""
        _out(f"Delivering the Hiring Signal Report for {client.display_name} ({client.name}){cap}{preview} ...")
        try:
            result = deliver(without_console_summary(client, pb), store=store, env=env, http=http,
                             today=date.today(), dry_run=bool(args.dry_run), budget=budget, out_dir=args.out,
                             log=log)
        except ClientError as e:
            raise CliError(str(e)) from e
    finally:
        store.close()
        close_http(http)
    _print_delivery(result, client, args, db_note)
    sourced = result.run.counts.get("sourced", 0)
    offline_sources, _ = _failed_sources(pb, [], True)
    if args.dry_run and not sourced and not offline_sources:
        _out("")
        _out("Note: nothing was found because every source uses the network and --dry-run skips them. "
             "Add a csv/json source to rehearse offline, or run without --dry-run.")
    active, failed = _failed_sources(pb, result.run.errors, bool(args.dry_run))
    if active and failed >= active and not sourced:
        sys.stderr.write(f"error: every source failed ({failed} of {active}) - see the warnings above\n")
        return EXIT_PROBLEM
    if not result.delivered:
        sys.stderr.write("error: no leads were delivered - see the QA summary above for why\n")
        return EXIT_PROBLEM
    return EXIT_OK


def cmd_clients(args: argparse.Namespace) -> int:
    if args.action == "new":
        return _clients_new(args)
    if args.name:
        raise CliError(f"'leadgen clients list' takes no name (got {args.name!r}). To create a client: "
                       f"leadgen clients new {args.name}")
    return _clients_list(args)


def _clients_new(args: argparse.Namespace) -> int:
    from .delivery.client import new_client_file

    name = str(args.name or "").strip()
    if not name:
        raise CliError("give the new client's short name, e.g. leadgen clients new acme-staffing "
                       "(lower-case letters, digits, '-' and '_')")
    if name.endswith((".yaml", ".yml")):
        name = name.rsplit(".", 1)[0]
    folder = Path(args.clients_dir)
    for existing in (folder / f"{name}.yaml", folder / f"{name}.yml"):
        if existing.exists():
            raise CliError(f"{existing} already exists - edit that file, or choose another name", EXIT_PROBLEM)
    try:
        path = new_client_file(name, args.clients_dir)
    except ClientError as e:
        raise CliError(str(e)) from e
    opt = "" if args.clients_dir == DEFAULT_CLIENTS_DIR else f" --clients-dir {shlex.quote(str(args.clients_dir))}"
    _out(f"Created {path} from the client template.")
    _out("")
    _out("Next steps:")
    _out(f"  1. Edit {path}: display_name, contact, roles, locations, leads_per_week, exclusions")
    _out("     (every setting is explained in the file).")
    _out(f"  2. leadgen doctor --client {name}{opt}             # checks the API keys it uses (free calls)")
    _out(f"  3. leadgen deliver --client {name}{opt} --dry-run   # preview files: nothing recorded, no charges")
    _out(f"  4. leadgen deliver --client {name}{opt}             # the real weekly delivery")
    return EXIT_OK


def _clients_list(args: argparse.Namespace) -> int:
    from .delivery.client import client_playbook, load_client
    from .delivery.ledger import Ledger

    env = build_env(args)
    folder = args.clients_dir
    names = list_clients(folder)
    stores: Dict[str, Optional[Store]] = {}

    def ledger_for(db: str) -> Optional[Any]:
        """The ledger in ``db``; None when that database does not exist yet (never created here)."""
        path = os.path.expanduser(str(db))
        key = path if path == ":memory:" else os.path.abspath(path)
        if key not in stores:
            stores[key] = open_store(path) if path != ":memory:" and Path(path).is_file() else None
        store = stores[key]
        return Ledger(store) if store is not None else None

    rows: List[List[Any]] = []
    problems: List[str] = []
    redeliver: List[str] = []   # clients whose file allows the same company / job / person again
    try:
        if getattr(args, "db", None):
            ledger_for(args.db)  # also lists clients delivered to before whose file is gone
        for name in names:
            try:
                client = load_client(name, folder)
                pb = client_playbook(client, env=env)   # also checks the base playbook is usable
                db = getattr(args, "db", None) or str(pb.db_path)
            except ClientError as e:
                problems.append(str(e))
                rows.append([name, "(invalid client file - see the error below)", "-", "-", "-", "-"])
                continue
            if client.redelivery_days is not None or not {"company", "job", "contact"} <= set(client.dedupe or []):
                redeliver.append(client.name)
            ledger = ledger_for(db)
            s = ledger.summary(client.name) if ledger is not None else {}
            rows.append([client.name, client.display_name, client.leads_per_week, s.get("deliveries", 0),
                         s.get("last_delivery") or "never", s.get("company", 0)])
        listed = set(names)
        for store in stores.values():  # clients delivered to before whose file is gone
            if store is None:
                continue
            ledger = Ledger(store)
            for other in ledger.list_clients_with_history():
                if other in listed:
                    continue
                listed.add(other)
                s = ledger.summary(other)
                rows.append([other, "(no client file)", "-", s.get("deliveries", 0),
                             s.get("last_delivery") or "never", s.get("company", 0)])
    finally:
        for store in stores.values():
            if store is not None:
                store.close()
    if not rows:
        where = f"{folder}/" if Path(folder).is_dir() else f"{folder}/ (the folder does not exist yet)"
        _out(f"No client files in {where}. Create one with: leadgen clients new <name>")
        return EXIT_OK
    _out(f"Clients in {folder}/: {len(names)}")
    _out("")
    _out(format_table(["client", "display name", "leads/week", "deliveries", "last delivery", "total delivered"],
                      rows, widths={"client": 30, "display name": 44},
                      right=("leads/week", "deliveries", "total delivered")))
    _out("")
    _out("total delivered = distinct companies sent so far (a company sent again is counted once).")
    if redeliver:
        _out(f"May get the same company / job / person again (their client file sets redelivery_days, or a "
             f"dedupe list without company / job / contact): {', '.join(redeliver)}. Every other client file "
             f"never allows it.")
    else:
        _out("No client file allows the same company, job or person to be delivered twice.")
    _out("Next: leadgen deliver --client <name> --dry-run   |   new client: leadgen clients new <name>")
    if problems:
        for p in problems:
            sys.stderr.write(f"error: {p}\n")
        return EXIT_PROBLEM
    return EXIT_OK


_DOCTOR_STATUS = {"ok": "ok", "failed": "FAILED", "missing_key": "MISSING KEY", "skipped": "skipped"}


def _doctor_adapter(r: Any) -> str:
    name = f"{r.kind} {r.type}"
    return name + (f" '{r.label}'" if r.label and r.label != r.type else "")


def cmd_doctor(args: argparse.Namespace) -> int:
    from .doctor import run_doctor

    env = build_env(args)
    pb_path = getattr(args, "playbook", None)
    if pb_path and args.client:
        raise CliError("doctor checks one thing at a time: give -p PLAYBOOK or --client NAME, not both")
    sheet: Optional[Dict[str, Any]] = None
    if args.client:
        client, pb = load_client_and_playbook(args, env)
        what = f"client '{client.name}' ({client.display_name}; base playbook {client.playbook})"
        sheet = dict(client.delivery.google_sheet or {})
        next_step = f"leadgen deliver --client {client.name} --dry-run"
    elif pb_path:
        pb = load_playbook(pb_path, env=env)
        what = f"playbook '{pb.name}' ({pb_path})"
        next_step = f"leadgen run -p {pb_path}"
    else:
        raise CliError("doctor needs -p PLAYBOOK or --client NAME (e.g. leadgen doctor --client acme, or "
                       "leadgen doctor -p playbooks/recruitment-delivery.yaml)")
    http = make_http()
    store = Store(":memory:")  # the doctor never reads or writes your database
    try:
        ctx = Context(playbook=pb, http=http, store=store, env=env, today=date.today(), log=log,
                      dry_run=bool(args.dry_run))
        _out(f"Checking the API keys of {what}, {mode_of(pb)} mode ...")
        if args.dry_run:
            _out("  (--dry-run: keys are only checked for presence - nobody is contacted)")
        else:
            _out("  (one free account / credits call per key - never a paid lookup)")
        results = run_doctor(ctx, google_sheet=sheet)
    finally:
        store.close()
        close_http(http)
    _out("")
    if not results:
        _out("Nothing to check: no enabled adapters.")
        return EXIT_OK
    rows = [[_DOCTOR_STATUS.get(r.status, r.status), _doctor_adapter(r), r.detail, r.quota or "-"]
            for r in results]
    _out(format_table(["status", "adapter", "detail", "quota"], rows,
                      widths={"adapter": 48, "detail": 240, "quota": 80}))
    counts = {s: sum(1 for r in results if r.status == s) for s in _DOCTOR_STATUS}
    problems = [r for r in results if r.status in ("failed", "missing_key")]
    _out("")
    _out(f"{counts['ok']} ok, {counts['failed']} failed, {counts['missing_key']} missing key, "
         f"{counts['skipped']} skipped")
    if problems:
        _out("Fix the FAILED / MISSING KEY rows: API keys go in .env (see .env.example), then run the doctor again.")
        sys.stderr.write(f"error: {len(problems)} adapter(s) need attention - see the table above\n")
        return EXIT_PROBLEM
    if counts["ok"]:
        _out(f"Every key that was checked works. Next: {next_step}")
    else:
        _out(f"Nothing here needs a key{' check' if args.dry_run else ''}. Next: {next_step}")
    return EXIT_OK


# =============================================================================
# replies / serve
# =============================================================================

REPLY_COLUMNS = ["received_at", "from_email", "subject", "category", "confidence", "summary",
                 "follow_up_date", "referral_name", "referral_email", "lead_id", "company", "action",
                 "suggested_reply", "body", "classifier"]


def cmd_replies(args: argparse.Namespace) -> int:
    gate = outbound_gate(args, "leadgen replies (reply handling)")
    if gate is not None:
        return gate
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
    gate = outbound_gate(args, "leadgen serve (the reply webhook server)")
    if gate is not None:
        return gate
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
        note = args.note or "manual"
        order = Stage.ORDER
        contacted = args.stage in order and order.index(args.stage) >= order.index(Stage.EXPORTED)
        if contacted and not store.was_exported(lead.id):
            # Dedupe (was_exported / recently_contacted / company cooldown) keys off the
            # hand-over time, not the stage: record it as an exporter would, or the next
            # run hands this person to the sending tool again.
            store.mark_exported(lead.id, note)
        now = store.get_lead(lead.id).stage
        if now == args.stage and now != before:
            changed = True
        else:
            changed = store.set_stage(lead.id, args.stage, note=note, force=args.force)
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


# host names: dot-separated labels (letters incl. IDN, digits, inner hyphens), TLD: 2+ chars, starts with a letter
_DOMAIN_RE = re.compile(r"^(?=.{3,253}$)(?:[^\W_](?:[\w-]{0,61}[^\W_])?\.)+[^\W\d_][\w-]{1,62}$")
# a LinkedIn profile / company page once normalised (no scheme, no www.): linkedin.com/in/jane-doe
_LINKEDIN_RE = re.compile(r"^(?:[a-z0-9-]+\.)*linkedin\.com/\S+$")
_CSV_DELIMITERS = (",", ";", "\t", "|")
# header words that mark a do-not-list column (compared without case / punctuation), per kind,
# tried group by group: email, e-mail, Email Address ...; domain / website; company name ...
_EMAIL_HEADER_WORDS = ("mail",)
_DOMAIN_HEADER_WORDS = ("domain", "website", "url", "site", "value")
_HEADER_WORDS: Dict[Optional[str], Tuple[Tuple[str, ...], ...]] = {
    None: (_EMAIL_HEADER_WORDS, _DOMAIN_HEADER_WORDS),
    "email": (_EMAIL_HEADER_WORDS, _DOMAIN_HEADER_WORDS),
    "domain": (_EMAIL_HEADER_WORDS, _DOMAIN_HEADER_WORDS),
    "company": (("company", "organisation", "organization", "employer", "business", "account"), ("name",)),
    "linkedin": (("linkedin",), ("profile", "url")),
}
_KIND_HINT = {
    None: "email address, domain or LinkedIn URL (for a company name add --kind company)",
    "email": "email address",
    "domain": "domain (e.g. acme.com)",
    "company": "company name",
    "linkedin": "LinkedIn profile URL (e.g. https://www.linkedin.com/in/jane-doe)",
}


def suppression_value(raw: Any, kind: Optional[str] = None) -> Optional[Tuple[str, str]]:
    """One do-not-list entry -> ``(value, kind)``, or None when it could never match.

    ``kind`` None = auto-detect: a LinkedIn URL (``linkedin.com/...``) is a
    ``linkedin`` entry, a value with an ``@`` an ``email``, anything else a
    ``domain``. Company names are never guessed: they need ``kind="company"``.

    Understands the usual blocklist notations: ``Jane Doe <jane@x.com>`` and
    ``mailto:jane@x.com`` (-> the address), ``@acme.com`` / ``*@acme.com`` /
    ``*.acme.com`` (-> the whole domain) and URLs (-> their domain). Emails and
    domains come back normalised; company names and LinkedIn URLs come back as
    typed (trimmed) - the store / ledger normalises them when saving and matching.
    Returns None for anything that is not a valid value of its kind (a person's
    name without ``--kind company``, a header cell, ``n/a`` ...).
    """
    v = str(raw or "").strip().strip("'\"").strip()
    if not v:
        return None
    if kind == "company":
        return (v, "company") if normalize_suppression(v, "company") else None
    if kind == "linkedin" or (kind is None and "linkedin.com/" in v.lower()):
        return (v, "linkedin") if _LINKEDIN_RE.match(normalize_suppression(v, "linkedin")) else None
    if ("<" in v and ">" in v) or v.lower().startswith("mailto:"):
        v = parseaddr(v)[1].strip() or v
    whole_domain = False
    if v.startswith("*"):                       # *@acme.com, *.acme.com
        v, whole_domain = v.lstrip("*"), True
    if v.startswith("@"):                       # @acme.com
        v, whole_domain = v[1:], True
    elif whole_domain and v.startswith("."):
        v = v[1:]
    k = kind or ("email" if "@" in v and "://" not in v and not whole_domain else "domain")
    if k == "email":
        v = v.lower()
        return (v, "email") if is_valid_email(v) else None
    d = normalize_domain(v)
    return (d, "domain") if _DOMAIN_RE.match(d) else None


def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(h or "").lower())


def _pick_column(rows: List[List[str]], kind: Optional[str] = None) -> Tuple[int, bool]:
    """``(column, has_header)`` holding the values of a do-not-list CSV.

    A header naming a column of the wanted kind wins (``email``, ``E-mail``,
    ``Email Address`` ..., then a domain / website column; for ``--kind company``
    a company column, for ``--kind linkedin`` a LinkedIn column); among several
    candidates the one with the most valid entries is used. Without a matching
    header the column with the most valid entries is used, and the first row
    counts as a header when its cell there is not a valid entry.
    """
    header = [_norm_header(h) for h in rows[0]]
    body = rows[1:]
    width = max(len(r) for r in rows)
    check_kind = kind if kind in ("company", "linkedin") else None

    def valid(col: int, rs: List[List[str]]) -> int:
        return sum(1 for r in rs if len(r) > col and suppression_value(r[col], check_kind))

    for words in _HEADER_WORDS.get(kind, _HEADER_WORDS[None]):
        cands = [i for i, h in enumerate(header) if h and any(w in h for w in words)]
        if cands:
            return max(cands, key=lambda i: (valid(i, body), -i)), True
    col = max(range(width), key=lambda i: (valid(i, rows), -i))
    first = rows[0][col] if len(rows[0]) > col else ""
    return col, suppression_value(first, check_kind) is None


def _read_values_file(path: Path, kind: Optional[str] = None) -> List[str]:
    """Values from a TXT (one per line, # comments) or a CSV / TSV export.

    CSV: the delimiter (``, ; tab |``) is taken from the header line and the
    column is found by ``_pick_column`` (so a Mailchimp-style ``Name,Email
    Address,Status`` export yields the addresses, not the names)."""
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() in (".csv", ".tsv"):
        lines = text.splitlines()
        first = next((ln for ln in lines if ln.strip()), "")
        if not first:
            return []
        delim = "\t" if path.suffix.lower() == ".tsv" else max(_CSV_DELIMITERS, key=first.count)
        if not first.count(delim):
            delim = ","
        rows = [r for r in csv.reader(lines, delimiter=delim) if any(c.strip() for c in r)]
        if not rows:
            return []
        col, has_header = _pick_column(rows, kind)
        body = rows[1:] if has_header else rows
        return [r[col].strip() for r in body if len(r) > col and r[col].strip()]
    out = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def _unsuppress(store: Store, raw: str, kind: Optional[str] = None) -> int:
    """Remove exactly one entry from the global list: an email removes only that address,
    a domain only the domain entry (``jane@acme.com`` never lifts a whole-domain block on
    ``acme.com``). The value as typed is removed too, so malformed legacy entries can be
    cleaned up. A value that is no email / domain / LinkedIn URL is tried as a company name."""
    targets = set()
    parsed = suppression_value(raw, kind)
    if parsed:
        targets.add((normalize_suppression(parsed[0], parsed[1]), parsed[1]))
    elif kind is None and normalize_suppression(str(raw or ""), "company"):
        targets.add((normalize_suppression(str(raw), "company"), "company"))
    exact = str(raw or "").strip().lower()
    if exact:
        targets.update((exact, k) for k in ([kind] if kind else ["email", "domain"]))
    removed = 0
    for value, k in sorted(targets):
        cur = store.conn.execute("DELETE FROM suppression WHERE value=? AND kind=?", (value, k))
        removed += max(0, cur.rowcount)
    store.conn.commit()
    return removed


def _suppress_values(args: argparse.Namespace) -> List[str]:
    """The VALUE argument plus the values of ``--file``."""
    values: List[str] = [args.value] if args.value else []
    if args.file:
        fp = Path(args.file)
        if not fp.is_file():
            raise FileNotFoundError(2, "file not found", str(fp))
        values += _read_values_file(fp, args.kind)
    if not values:
        raise CliError(f"suppress {args.action}: give a VALUE or --file")
    return values


def _report_invalid(invalid: List[str], kind: Optional[str]) -> None:
    for v in invalid[:10]:
        sys.stderr.write(f"warning: skipped {v!r} - not a valid {_KIND_HINT.get(kind, kind)}\n")
    if len(invalid) > 10:
        sys.stderr.write(f"warning: ... and {len(invalid) - 10} more invalid value(s)\n")


def _suppression_table(rows: List[Dict[str, Any]]) -> str:
    return format_table(["value", "kind", "reason", "added"],
                        [[r["value"], r["kind"], r.get("reason") or "", r.get("added_at") or ""] for r in rows],
                        widths={"value": 60, "reason": 40})


def _client_target(args: argparse.Namespace, env: Dict[str, str]) -> Tuple[str, str, Optional[Client]]:
    """``(client name, database, Client or None)`` for ``suppress --client NAME``.

    The database is ``--db``, else the storage path of the client's playbook (the one
    ``leadgen deliver`` records in). ``add`` needs an existing client file (a typo must
    not start a list nobody reads); ``remove`` / ``list`` also work for a client whose
    file is gone (then ``--db`` or the default database is used)."""
    from .delivery.client import client_playbook, load_client

    raw = client_arg(args.client, args.clients_dir)
    p = Path(raw)
    name = p.stem if p.suffix.lower() in (".yaml", ".yml") else p.name
    known = list_clients(args.clients_dir)
    exists = name in known or (p.suffix.lower() in (".yaml", ".yml") and p.is_file())
    db = getattr(args, "db", None)
    if not exists:
        if args.action == "add":
            msg = f"client '{raw}' not found in {args.clients_dir}/"
            guess = difflib.get_close_matches(name, known, n=1, cutoff=0.6)
            if guess:
                msg += f" - did you mean '{guess[0]}'?"
            elif known:
                msg += f" (clients: {', '.join(known)})"
            raise CliError(msg + f". Create it with: leadgen clients new {name}")
        return name, db or DEFAULT_DB, None
    try:
        client = load_client(raw, args.clients_dir)
        if not db:
            db = str(client_playbook(client, env=env).db_path)
    except ClientError as e:
        raise CliError(str(e)) from e
    return client.name, db, client


def _suppress_client(args: argparse.Namespace) -> int:
    """``suppress ... --client NAME``: that client's own do-not-list (the delivery ledger)."""
    from .delivery.ledger import Ledger

    name, db, client = _client_target(args, build_env(args))
    store = open_store(db)
    try:
        ledger = Ledger(store)
        whose = f"client '{name}'"
        if args.action == "list":
            rows = ledger.list_suppressed(name)
            if rows:
                _out(f"{len(rows)} value(s) on the do-not-list of {whose} in {store.path}:")
                _out("")
                _out(_suppression_table(rows))
            else:
                _out(f"The do-not-list of {whose} in {store.path} is empty.")
            if client is not None:
                ex = client.exclusions
                extra = [f"{label}: {', '.join(map(str, items))}" for label, items in
                         (("companies", ex.companies), ("domains", ex.domains), ("keywords", ex.keywords)) if items]
                if extra:
                    _out("")
                    _out(f"Also left out by the client file ({client.path or name}) 'exclusions': " + "; ".join(extra))
            return EXIT_OK
        values = _suppress_values(args)
        done = 0
        invalid: List[str] = []
        for v in values:
            parsed = suppression_value(v, args.kind)
            if args.action == "add":
                if parsed is None:
                    invalid.append(v)
                    continue
                try:
                    ledger.suppress(name, parsed[0], parsed[1], args.reason or "manual")
                except ValueError as e:
                    log.debug("not added: %s", e)
                    invalid.append(v)
                    continue
                done += 1
            elif parsed is not None:
                done += ledger.unsuppress(name, parsed[0], parsed[1])
            elif args.kind is None and normalize_suppression(v, "company"):
                done += ledger.unsuppress(name, v, "company")   # a company name typed without --kind
        _report_invalid(invalid, args.kind)
        verb, prep = ("Added", "to") if args.action == "add" else ("Removed", "from")
        _out(f"{verb} {done} value(s)" + (f" (of {len(values)} given)" if done != len(values) else "")
             + f" {prep} the do-not-list of {whose} in {store.path}.")
        if args.action == "add" and done:
            _out(f"They are never delivered to {name} (checked before any paid lookup).")
        if invalid:
            sys.stderr.write(f"error: {len(invalid)} value(s) were not added - fix them and add them again "
                             f"(e.g. jane@acme.com, acme.com, a LinkedIn URL, or --kind company \"Acme Corp\")\n")
            return EXIT_PROBLEM
        return EXIT_OK
    finally:
        store.close()


def cmd_suppress(args: argparse.Namespace) -> int:
    if args.client:
        if getattr(args, "playbook", None):
            log.warning("-p is ignored with --client: the client's list lives in its playbook's database")
        return _suppress_client(args)
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
            _out(_suppression_table(rows))
            return EXIT_OK
        values = _suppress_values(args)
        done = 0
        invalid: List[str] = []
        for v in values:
            if args.action == "add":
                parsed = suppression_value(v, args.kind)
                if parsed is None:
                    invalid.append(v)
                    continue
                store.suppress(parsed[0], parsed[1], args.reason or "manual")
                done += 1
            else:
                done += _unsuppress(store, v, args.kind)
        _report_invalid(invalid, args.kind)
        verb = "Added" if args.action == "add" else "Removed"
        _out(f"{verb} {done} value(s)" + (f" (of {len(values)} given)" if done != len(values) else "")
             + f" {'to' if args.action == 'add' else 'from'} the suppression list in {store.path}.")
        if invalid:
            sys.stderr.write(f"error: {len(invalid)} value(s) were not added - fix them and add them "
                             f"again (e.g. jane@acme.com or acme.com, or --kind company \"Acme Corp\")\n")
            return EXIT_PROBLEM
        return EXIT_OK
    finally:
        session.close()


def cmd_followups(args: argparse.Namespace) -> int:
    gate = outbound_gate(args, "leadgen followups")
    if gate is not None:
        return gate
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


def adapter_notes(kind: str, name: str, cls: Any = None) -> List[str]:
    """Notes shown by ``leadgen adapters``: the 'use at own risk' note of scrapers
    (``registry.risk_note``) and whether the adapter only works in outbound mode."""
    notes: List[str] = []
    risk = registry.risk_note(kind, name)
    if risk:
        notes.append(risk)
    if kind == "exporter" and cls is not None and getattr(cls, "scope", "all") == "outbound":
        notes.append("outbound mode only: hands leads to a sending tool")
    if kind == "writer":
        notes.append("outbound mode only: writes email copy")
    return notes


def cmd_adapters(args: argparse.Namespace) -> int:
    _out("Adapter types by kind (use them as 'type:' in a playbook).")
    _out("runs: offline = never uses the network; network (paid) = every request costs money or credits "
         "and counts towards --budget / usage.max_paid_lookups.")
    for kind in registry.KINDS:
        _out("")
        _out(f"{kind}:")
        rows = []
        for name in registry.available(kind):
            cls = None
            try:
                cls = registry.resolve(kind, name)
                env = getattr(cls, "env_key", "") or ""
                where = "offline" if getattr(cls, "offline", False) else "network"
                if where == "network" and registry.is_paid(kind, name):
                    where = "network (paid)"
                doc = (cls.__doc__ or "").strip().splitlines()[0] if cls.__doc__ else ""
            except Exception as e:  # noqa: BLE001 - a broken optional adapter must not hide the rest
                env, where, doc = "", "unavailable", f"cannot import: {e}"
            creds = CREDENTIALS.get((kind, name))
            if creds:
                env = ", ".join(e for _, e in creds)
            if (kind, name) == ("llm", "openai_compatible"):
                env = "writer.api_key_env"  # never OPENAI_API_KEY: that key only goes to api.openai.com
            notes = "; ".join(adapter_notes(kind, name, cls))
            rows.append(["  " + name, where, env or "-", doc, notes or "-"])
        _out(format_table(["type", "runs", "credential", "description", "notes"], rows,
                          widths={"description": 60, "notes": 90}))
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


def _add_budget(sp: argparse.ArgumentParser, what: str) -> None:
    sp.add_argument("--budget", type=int, metavar="N",
                    help=f"at most N paid lookups (requests to paid providers) this run; 0 = no cap. "
                         f"Overrides {what}. For no paid calls at all use --dry-run")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="leadgen",
        description="Signal-led lead generation: find companies with a reason to buy now (e.g. they are "
                    "hiring), find the decision-maker, verify the email, score, and deliver lead files "
                    "(delivery mode) or run outreach (outbound mode).",
        epilog="Start here: leadgen deliver --client demo-client --dry-run   or   "
               "leadgen run -p playbooks/demo-delivery.yaml   (no API keys needed)")
    parser.add_argument("--version", action="version", version=f"leadgen {__version__}")
    _add_global_options(parser, suppress=False)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    parent = argparse.ArgumentParser(add_help=False)
    _add_global_options(parent, suppress=True)

    def add(name: str, func: Callable[[argparse.Namespace], int], help_text: str) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, parents=[parent], help=help_text, description=help_text)
        sp.set_defaults(func=func)
        return sp

    def clients_dir(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--clients-dir", default=DEFAULT_CLIENTS_DIR, metavar="DIR",
                        help=f"folder with the client files (default: {DEFAULT_CLIENTS_DIR})")

    sp = add("init", cmd_init, "create a new playbook from a template")
    sp.add_argument("name", help="playbook name (letters, digits, - _ .)")
    sp.add_argument("--template", choices=TEMPLATES, default="generic", help="template to copy (default: generic)")
    sp.add_argument("--dir", default="playbooks", help="where to write it (default: playbooks)")

    sp = add("validate", cmd_validate, "check a playbook: mode, adapters, config and API keys")
    sp.add_argument("--dry-run", action="store_true",
                    help="check for a --dry-run (keys of network adapters become optional)")

    sp = add("run", cmd_run, "run the pipeline once (delivery mode: up to scored lead files; "
                             "outbound mode: also write + hand over)")
    sp.add_argument("--out", metavar="DIR", help="output folder (default: output/<playbook>/); "
                                                 "each run gets its own sub-folder")
    sp.add_argument("--limit", type=int, metavar="N", help="only enrich/score the top N companies (cheap test)")
    sp.add_argument("--dry-run", action="store_true",
                    help="skip everything that uses the network (no paid API calls, nothing sent)")
    _add_budget(sp, "the playbook's usage.max_paid_lookups")

    sp = add("deliver", cmd_deliver, "make one client's weekly Hiring Signal Report (CSV / Excel / HTML)")
    sp.add_argument("--client", required=True, metavar="NAME",
                    help="the client file: NAME = clients/NAME.yaml (see: leadgen clients)")
    clients_dir(sp)
    sp.add_argument("--dry-run", action="store_true",
                    help="preview: offline sources only, files named ...-PREVIEW, nothing recorded as "
                         "delivered, no Google Sheets push, no paid calls")
    _add_budget(sp, "the client file's budget.max_paid_lookups and the playbook's usage.max_paid_lookups")
    sp.add_argument("--out", metavar="DIR",
                    help="output folder (default: the client file's delivery.folder, "
                         "deliveries/{client}/{date}); {client} and {date} are filled in")

    sp = add("clients", cmd_clients, "list your clients (volume + delivery history), or create a client file")
    sp.add_argument("action", nargs="?", choices=("list", "new"), default="list",
                    help="list (default) or new")
    sp.add_argument("name", nargs="?", help="new: the client's short name (lower-case letters, digits, - and _)")
    clients_dir(sp)

    sp = add("doctor", cmd_doctor, "test every API key a playbook (-p) or client (--client) uses - "
                                   "one free call per key, never a paid lookup")
    sp.add_argument("--client", metavar="NAME", help="check the keys this client's delivery uses "
                                                     "(instead of -p PLAYBOOK)")
    clients_dir(sp)
    sp.add_argument("--dry-run", action="store_true", help="only check that keys are set; contact nobody")

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

    sp = add("replies", cmd_replies, "(outbound mode) classify a replies CSV and act on each reply")
    sp.add_argument("--file", required=True, metavar="CSV", help="replies export (from_email, subject, body, ...)")
    sp.add_argument("--out", metavar="DIR", help="where replies_classified.csv goes (default: output/<playbook>/)")

    sp = add("serve", cmd_serve, "(outbound mode) run the reply webhook server (Instantly / Smartlead / generic)")
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

    sp = add("suppress", cmd_suppress, "manage the do-not-list: global, or one client's with --client")
    sp.add_argument("action", choices=("add", "remove", "list"))
    sp.add_argument("value", nargs="?", help="an email address, a domain, a LinkedIn profile URL, or (with "
                                             "--kind company) a company name. Also 'Name <email>', "
                                             "'@domain' / '*@domain' for a whole domain")
    sp.add_argument("--kind", choices=SUPPRESSION_KINDS,
                    help="default: linkedin for a linkedin.com URL, email if it contains @, else domain "
                         "(company names always need --kind company)")
    sp.add_argument("--client", metavar="NAME", help="this client's own do-not-list (never delivered to "
                                                     "them) instead of the global list")
    clients_dir(sp)
    sp.add_argument("--reason", help="why (shown in the list)")
    sp.add_argument("--file", metavar="CSV/TXT", help="many values: one per line, or a CSV / TSV export "
                                                      "(its email - else domain / website - column is used; "
                                                      "with --kind company its company column)")

    sp = add("followups", cmd_followups, "(outbound mode) list follow-ups that are due "
                                         "(from timing / out-of-office replies)")
    sp.add_argument("--done", type=int, metavar="ID", help="mark this follow-up as done")
    sp.add_argument("--days", type=int, default=0, help="also show those due in the next N days")

    add("adapters", cmd_adapters, "list every adapter type per kind (offline / network / paid, key, notes)")
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
    except (CliError, PlaybookError, ClientError, MissingCredentialError, FileNotFoundError,
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
