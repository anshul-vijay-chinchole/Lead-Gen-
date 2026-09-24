"""Run context + the ``Adapter`` base class every plugin inherits from."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, Optional

from .http import HttpClient
from .playbook import Playbook


class MissingCredentialError(RuntimeError):
    """An adapter needs an API key that is not configured."""


@dataclass
class Context:
    """Everything an adapter may need. Built once per command."""

    playbook: Playbook
    http: Any = None                 # HttpClient or a test fake with the same interface
    store: Any = None                # leadgen.store.Store
    env: Dict[str, str] = field(default_factory=lambda: dict(os.environ))
    today: date = field(default_factory=date.today)
    log: logging.Logger = field(default_factory=lambda: logging.getLogger("leadgen"))
    dry_run: bool = False            # no paid API calls / no sending; stages that need them are skipped
    _llm: Any = None
    _llm_loaded: bool = False

    def __post_init__(self) -> None:
        if self.http is None:
            self.http = HttpClient()

    @property
    def llm(self) -> Any:
        """LLM client configured in ``writer`` (or None if not configured / no key)."""
        if not self._llm_loaded:
            self._llm_loaded = True
            from .llm import build_llm  # local import: optional feature
            try:
                self._llm = build_llm(self)
            except MissingCredentialError as e:
                self.log.warning("LLM disabled: %s", e)
                self._llm = None
        return self._llm

    @llm.setter
    def llm(self, value: Any) -> None:
        self._llm, self._llm_loaded = value, True


class Adapter:
    """Base for sources, finders, verifiers, exporters, notifiers, writers, LLM clients.

    Subclasses set ``name`` and (optionally) ``env_key`` - the environment
    variable holding their API key. Config may override with ``api_key`` or
    ``api_key_env``.
    """

    name: str = "adapter"
    env_key: str = ""
    # True when the adapter never touches the network (local files, pure logic).
    # In ``ctx.dry_run`` only offline adapters run.
    offline: bool = False

    def __init__(self, config: Dict[str, Any], ctx: Context):
        self.config = config or {}
        self.ctx = ctx

    @property
    def http(self) -> Any:
        return self.ctx.http

    @property
    def log(self) -> logging.Logger:
        return self.ctx.log

    def secret(self, config_key: str = "api_key", env_key: Optional[str] = None,
               required: bool = True) -> str:
        """Resolve a credential: config value > config '<key>_env' var > default env var."""
        val = str(self.config.get(config_key) or "").strip()
        if val:
            return val
        env_name = self.config.get(f"{config_key}_env") or env_key or self.env_key
        env_val = str(self.ctx.env.get(env_name) or "").strip() if env_name else ""
        if env_val:
            return env_val
        if required:
            hint = f"set ${env_name}" if env_name else f"set '{config_key}' in the playbook"
            raise MissingCredentialError(f"{self.name}: missing credential ({hint})")
        return ""

    def has_secret(self, config_key: str = "api_key", env_key: Optional[str] = None) -> bool:
        return bool(self.secret(config_key, env_key, required=False))
