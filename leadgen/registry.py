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


class UnknownAdapterError(KeyError):
    pass


def register(kind: str, name: str, target: str) -> None:
    """Register (or override) an adapter: register('source', 'mine', 'pkg.mod:Cls')."""
    if kind not in KINDS:
        raise ValueError(f"unknown adapter kind {kind!r}; expected one of {KINDS}")
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
    cls = resolve(kind, str(config.get("type")))
    return cls(dict(config), ctx)
