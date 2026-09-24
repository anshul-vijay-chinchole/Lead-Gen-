# Architecture & module contracts

This is the contract every module is built against. The foundation files are
already written and are the source of truth — read them before coding:

| File | What it is |
|---|---|
| `leadgen/models.py` | `Signal`, `Contact`, `Company`, `Lead`, `Message`, `ScoreBreakdown`, `Reply` + vocab classes `SignalType`, `EmailStatus`, `Tier`, `Stage`, `ReplyCategory` |
| `leadgen/utils.py` | normalisers (`normalize_text`, `normalize_domain`, `normalize_company_name`, `company_key`), `parse_date`, `to_int`, `is_valid_email`, `is_personal_email`, `contains_any` (word-boundary match), `get_path` (dotted paths), `word_count`, `truncate` |
| `leadgen/playbook.py` | `Playbook` + `DEFAULTS` (every config key and its default lives here), `from_dict`, `load_playbook` |
| `leadgen/context.py` | `Context` (playbook, http, store, env, today, log, dry_run, llm) and `Adapter` base (`secret()`, `has_secret()`, `offline`) |
| `leadgen/registry.py` | maps playbook `type:` names → `"module:Class"`; **the module paths/class names listed there are binding** |
| `leadgen/http.py` | `HttpClient` (`request/get/post/get_json/post_json`, raises `HttpError`) |
| `leadgen/store.py` | SQLite `Store` (runs, signal history, verification cache, leads + stages, suppression, replies, follow-ups, funnel) |
| `leadgen/pipeline.py` | orchestrator — shows exactly how every module below is called |
| `leadgen/*/base.py` | base classes: `Source`, `ContactFinder`, `Verifier`+`VerificationResult`, `LLMClient`+`parse_json_block`, `Writer`+`WriterOutput`, `Exporter`+`ExportResult`, `Notifier` |
| `tests/fakes.py` | `FakeHttp` (route canned responses, records `calls`), `FakeLLM` |
| `tests/conftest.py` | `make_ctx(env=..., dry_run=..., **playbook_overrides)` fixture, `TODAY = 2026-09-24` |

## Flow

```
sources ─► merge_companies ─► process_signals ─► apply_icp ─► pre-score ─► ContactWaterfall.enrich
   ─► select_contacts ─► verify (pipeline.verify_contact) ─► score/tier_for ─► writer.write
   ─► store.save_lead ─► exporters (scope all / outbound) ─► notify(run_summary)
```

## Rules for every adapter

* Constructor is always `__init__(self, config: dict, ctx: Context)` (inherited from `Adapter`).
* Class attrs: `name` (= registry type), `env_key` (default env var for the API key, e.g. `APOLLO_API_KEY`), `offline = True` only if it never touches the network.
* API keys: `self.secret()` (config `api_key` > config `api_key_env` > `env_key`). Missing → `MissingCredentialError` (raised lazily at first use, **not** in `__init__`, so `leadgen validate` can instantiate adapters).
* All HTTP through `self.http` (`ctx.http`) — never import `requests` in adapters. Parse responses defensively (`.get`, `utils.get_path`): providers add/remove fields.
* Respect config `limit` / pagination caps; never loop forever on pagination.
* No `print` — use `self.log`.
* Unit tests use `tests.fakes.FakeHttp` with realistic canned payloads that mirror the provider's documented response shape, and assert on request shape (URL, params/json, auth header).

## Module contracts (signatures the pipeline/CLI call)

### Core logic
* `leadgen/signals.py`
  * `process_signals(companies, ctx) -> (kept: List[Company], rejected: List[Tuple[Company, str]])`
    — per company: drop signals not in `signals.types` (if set), whose title matches `signals.exclude_keywords`, or (primary types only) not matching `signals.match_keywords` (if set); call `ctx.store.observe_signals(company.key, signals, ctx.today)`; drop signals older than `max_age_days` (`Signal.age_days`); sort (primary first, then freshest). Reject the company with a readable reason if `signals.require` and nothing is left.
  * `signal_stats(company, ctx) -> dict` with keys: `primary` (list of primary signals), `secondary` (list), `freshest_age` (int|None), `volume` (count primary), `persistent` (bool: any reposted or age ≥ `stale_after_days`), `urgent` (bool: urgency keyword in title/description), `secondary_types` (set).
* `leadgen/filters.py`
  * `apply_icp(companies, ctx) -> (kept, rejected)` and `check_icp(company, ctx) -> Optional[str]` (reason or None)
  * `fit_checks(company, ctx) -> Dict[str, Optional[bool]]` keys `location`, `size`, `industry`: True match / False mismatch / None unknown data; criterion not configured → True.
* `leadgen/scoring.py` — `score(company, contact: Optional[Contact], ctx) -> ScoreBreakdown`, `tier_for(total, ctx) -> str`.
* `leadgen/contacts.py` — `title_rank(title, ctx) -> Optional[int]`, `is_generic_email(email) -> bool`, `select_contacts(company, ctx, limit) -> List[Contact]`, `class ContactWaterfall(ctx, errors: Optional[list] = None)` with `.enrich(company) -> List[str]`.

### Writers / LLM
* `leadgen/writer/__init__.py` must export `build_writer(ctx) -> Writer` (AI writer when `writer.type == "ai"`, an LLM is available and not dry-run; else template writer).
* `Writer.write(lead) -> WriterOutput` — one `Message` per `writer.sequence` step.

### Exporters
* `Exporter.export(leads, out_dir) -> ExportResult`; `scope = "outbound"` for anything that hands leads to a sending tool (CSV-for-Instantly counts), `"all"` for review sheets.

### Replies / reports / CLI (see their module docstrings once written)
* `leadgen/replies.py` — classify + act on replies.
* `leadgen/report.py` — demo report + funnel stats.
* `leadgen/cli.py` — `main(argv=None) -> int`.
* `leadgen/server.py` — webhook receiver for reply events.
