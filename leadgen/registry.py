"""Adapter registry.

Adapters are referenced from playbooks by ``type`` (e.g. ``{type: apollo}``).
The registry maps (kind, type) -> "module:Class" and imports lazily, so a
missing optional dependency in one adapter never breaks the others.
"""
from __future__ import annotations

import importlib
from typing import Any, Dict, List

KINDS = ("source", "finder", "verifier", "llm", "writer", "exporter", "notifier")

_REGISTRY: Dict[str, Dict[str, str]] = {
    "source": {
        "csv": "leadgen.sources.csv_source:CsvSource",
        "json": "leadgen.sources.csv_source:JsonSource",
        "apify": "leadgen.sources.apify:ApifySource",
        "linkedin_jobs": "leadgen.sources.apify:LinkedInJobsSource",
        "apollo": "leadgen.sources.apollo:ApolloSource",
        "adzuna": "leadgen.sources.adzuna:AdzunaSource",
        "theirstack": "leadgen.sources.theirstack:TheirStackSource",
        "greenhouse": "leadgen.sources.ats:GreenhouseSource",
        "lever": "leadgen.sources.ats:LeverSource",
        "ashby": "leadgen.sources.ats:AshbySource",
    },
    "finder": {
        "apollo": "leadgen.enrich.apollo:ApolloFinder",
        "hunter": "leadgen.enrich.hunter:HunterFinder",
        "pattern": "leadgen.enrich.pattern:PatternFinder",
        "csv": "leadgen.enrich.csv_finder:CsvFinder",
    },
    "verifier": {
        "basic": "leadgen.verify.basic:BasicVerifier",
        "millionverifier": "leadgen.verify.millionverifier:MillionVerifier",
        "zerobounce": "leadgen.verify.zerobounce:ZeroBounceVerifier",
        "neverbounce": "leadgen.verify.neverbounce:NeverBounceVerifier",
        "hunter": "leadgen.verify.hunter:HunterVerifier",
    },
    "llm": {
        "openai": "leadgen.llm.openai:OpenAIClient",
        "openai_compatible": "leadgen.llm.openai:OpenAIClient",
        "anthropic": "leadgen.llm.anthropic:AnthropicClient",
    },
    "writer": {
        "template": "leadgen.writer.template:TemplateWriter",
        "ai": "leadgen.writer.ai:AIWriter",
    },
    "exporter": {
        "csv": "leadgen.outbound.csv_export:CsvExporter",
        "json": "leadgen.outbound.csv_export:JsonExporter",
        "instantly_csv": "leadgen.outbound.instantly:InstantlyCsvExporter",
        "instantly": "leadgen.outbound.instantly:InstantlyApiExporter",
        "smartlead_csv": "leadgen.outbound.smartlead:SmartleadCsvExporter",
        "smartlead": "leadgen.outbound.smartlead:SmartleadApiExporter",
        "gsheets": "leadgen.outbound.gsheets:GoogleSheetsExporter",
        "webhook": "leadgen.outbound.webhook:WebhookExporter",
    },
    "notifier": {
        "console": "leadgen.notify.console:ConsoleNotifier",
        "slack": "leadgen.notify.slack:SlackNotifier",
        "webhook": "leadgen.notify.webhook:WebhookNotifier",
    },
}


# Adapter types whose requests cost money / credits ("paid lookups", see usage.py).
# Plugins add theirs with register(..., paid=True).
PAID = frozenset({
    ("source", "apollo"), ("source", "theirstack"), ("source", "apify"), ("source", "linkedin_jobs"),
    ("finder", "apollo"), ("finder", "hunter"),
    ("verifier", "millionverifier"), ("verifier", "zerobounce"), ("verifier", "neverbounce"),
    ("verifier", "hunter"),
    ("llm", "openai"), ("llm", "openai_compatible"), ("llm", "anthropic"),
})

# Adapters that scrape sites whose terms forbid it. Shown by `leadgen adapters`,
# warned about by `validate` / at run time, and never used by the shipped default
# playbooks. Apify is flagged per config (see risk_note).
RISK_USE_AT_OWN_RISK = "use at own risk: scrapes a site whose terms forbid scraping"
RISKS = {
    ("source", "linkedin_jobs"): "use at own risk: scrapes LinkedIn via Apify (against LinkedIn's terms)",
}
_RISKY_APIFY_PRESETS = {"linkedin_jobs": "LinkedIn", "indeed_jobs": "Indeed"}
_PLUGIN_PAID: set = set()


def is_paid(kind: str, name: str) -> bool:
    return (kind, name) in PAID or (kind, name) in _PLUGIN_PAID


def risk_note(kind: str, name: str, config: Any = None) -> str:
    """'' or a 'use at own risk' note for this adapter (+ config)."""
    if (kind, name) in RISKS:
        return RISKS[(kind, name)]
    if kind == "source" and name == "apify":
        cfg = config if isinstance(config, dict) else {}
        preset = str(cfg.get("preset") or "").lower()
        actor = str(cfg.get("actor") or "").lower()
        site = _RISKY_APIFY_PRESETS.get(preset) or next(
            (label for key, label in (("linkedin", "LinkedIn"), ("indeed", "Indeed")) if key in actor), "")
        if site:
            return f"use at own risk: scrapes {site} via Apify (against {site}'s terms)"
        if config is None:
            return "scraper platform: the linkedin_jobs / indeed_jobs presets are use-at-own-risk"
    return ""


class UnknownAdapterError(KeyError):
    pass


def register(kind: str, name: str, target: str, paid: bool = False) -> None:
    """Register (or override) an adapter: register('source', 'mine', 'pkg.mod:Cls').

    ``paid=True`` makes its requests count as paid lookups (capped by --budget)."""
    if kind not in KINDS:
        raise ValueError(f"unknown adapter kind {kind!r}; expected one of {KINDS}")
    if paid:
        _PLUGIN_PAID.add((kind, name))
    else:
        _PLUGIN_PAID.discard((kind, name))
    _REGISTRY[kind][name] = target


def available(kind: str) -> List[str]:
    return sorted(_REGISTRY.get(kind, {}))


def resolve(kind: str, name: str) -> Any:
    try:
        target = _REGISTRY[kind][name]
    except KeyError:
        raise UnknownAdapterError(
            f"unknown {kind} type {name!r}. Available: {', '.join(available(kind)) or 'none'}"
        ) from None
    module_name, _, attr = target.partition(":")
    module = importlib.import_module(module_name)
    return getattr(module, attr)


def create(kind: str, config: Dict[str, Any], ctx: Any) -> Any:
    """Instantiate the adapter named by ``config['type']``."""
    name = str(config.get("type"))
    cls = resolve(kind, name)
    adapter = cls(dict(config), ctx)
    adapter.adapter_kind, adapter.type_name = kind, name
    adapter.paid = is_paid(kind, name)
    return adapter
