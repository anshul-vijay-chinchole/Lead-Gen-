# Architecture & module contracts

How the engine is put together, and the promises each part makes to the
others. The code is the source of truth: every module starts with a docstring
that lists the playbook keys it reads, and `leadgen/playbook.py` `DEFAULTS`
holds every setting with its default (`playbooks/templates/generic.yaml`
explains each one in plain English). The client-delivery layer has its own
binding contract: [`docs/DELIVERY.md`](DELIVERY.md).

The engine is **niche-agnostic** and **country-agnostic**: nothing about a
niche, a country or a legal regime is hard-coded. Everything that changes
between niches lives in a playbook (YAML); everything that changes between
clients lives in a client file (YAML).

## Modes

Every playbook runs in one of two modes (`mode:`, default `delivery`;
`leadgen/modes.py`):

| | `delivery` (default) | `outbound` |
|---|---|---|
| The business | Sell lead files (the weekly Hiring Signal Report) | Run outreach yourself |
| Pipeline | source -> signals -> ICP -> enrich -> verify -> score -> **review export** | ... + **write** sequences + **hand over** to sending tools |
| WRITE stage | skipped: `build_writer` is never called | runs |
| Hand-over exporters (`scope = "outbound"`: instantly(_csv), smartlead(_csv), webhook) | never built (a warning names them) | run first, record EXPORTED |
| `replies`, `serve`, `followups` | refused: exit 2, nothing opened | work |

The guard is layered. `modes.require_outbound(ctx, feature)` raises
`OutboundOnlyError` inside `replies.handle_reply` and `server.make_server`.
`Pipeline` checks `is_outbound(ctx)` before the WRITE and hand-over stages.
The CLI's `outbound_gate` refuses `replies` / `serve` / `followups` before it
opens a database or the network, and without `-p` the default mode (delivery)
applies. `client_playbook` always forces `mode: delivery` for client
deliveries and strips hand-over exporters, whatever the base playbook says.

## Module layout

```
leadgen/
  cli.py          `leadgen <command>`: init, validate, run, deliver, clients, doctor, demo, leads,
                  replies, serve, stats, mark, suppress, followups, adapters. main(argv) -> exit code;
                  make_http() is the one HTTP factory (tests replace it with FakeHttp)
  modes.py        DELIVERY / OUTBOUND, mode_of, is_outbound, require_outbound, OutboundOnlyError
  playbook.py     Playbook, DEFAULTS, load_playbook / from_dict (merge over defaults,
                  expand ${ENV} / ${ENV:-default}, validate)
  context.py      Context (playbook, http, store, env, today, log, dry_run, usage, llm) and the
                  Adapter base class (secret(), has_secret(), offline, metered http)
  registry.py     playbook `type:` name -> "module:Class", imported lazily; register();
                  PAID (types whose requests are paid lookups); risk_note() (use-at-own-risk)
  usage.py        UsageMeter (calls / paid lookups / tokens / credits per adapter, budget,
                  cost estimate), MeteredHttp, BudgetExceeded, LLM_PRICES
  http.py         HttpClient (retry/backoff on 429 + 5xx), HttpError, redact(), safe_url()
  models.py       Signal, Contact, Company, Lead, Message, ScoreBreakdown, Reply +
                  vocabularies SignalType, EmailStatus, Tier, Stage, ReplyCategory
  utils.py        normalisers (text, domain, company name, company_key), parse_date,
                  to_int, is_valid_email, is_personal_email, contains_any, get_path, ...
  store.py        SQLite Store - the engine's memory between runs
  pipeline.py     Pipeline(ctx, out_dir, limit, hooks).run() - one full run, stage by stage;
                  PipelineHooks; RunResult
  signals.py      stage 2: process_signals, signal_stats, keyword_match (the one matcher)
  filters.py      stage 3: apply_icp, check_icp, fit_checks, excluded_domain
  contacts.py     stage 5: ContactWaterfall, select_contacts, title_rank, is_generic_email
  scoring.py      stage 7: score, tier_for
  doctor.py       leadgen doctor: run_doctor(ctx, google_sheet=None) -> [DoctorResult]
  delivery/       the client-delivery layer (see below and docs/DELIVERY.md)
    client.py     Client, load_client, list_clients, client_playbook, new_client_file
    ledger.py     Ledger (per-client delivery history + do-not-list), LedgerHooks, item keys
    rows.py       the client row: columns, email_label (4 honest labels), build_row, DeliveryPackage
    formats.py    write_csv / write_xlsx / write_html / push_google_sheet / write_all
    opening.py    template_line, add_opening_lines (AI with a cost cap), OpeningStats
    qa.py         QAReport, build_qa
    run.py        deliver(client, ...) -> DeliveryResult; select_leads
  replies.py      (outbound) classify + act on replies (rules / ai / auto)
  server.py       (outbound) webhook receiver for reply events (make_server)
  report.py       demo one-pager (write_demo), funnel_report, runs_report
  sources/        base, csv_source (csv, json), mapping (column detection + helpers),
                  theirstack, adzuna, apollo, apify (+ linkedin_jobs), ats (greenhouse, lever, ashby)
  enrich/         base, csv_finder, apollo, hunter, pattern          (contact finders)
  verify/         base, basic, millionverifier, zerobounce, neverbounce, hunter
  llm/            base, openai (openai + openai_compatible), anthropic
  writer/         (outbound) base, template, ai, prompts, guardrails
  outbound/       base, csv_export (csv, json, upload-CSV base), instantly (instantly_csv,
                  instantly), smartlead (smartlead_csv, smartlead), gsheets, webhook
  notify/         base, console, slack, webhook + the notify() dispatcher
clients/          one YAML per client (_template.yaml documents every key; demo-client.yaml)
playbooks/        recruitment-delivery.yaml, demo-delivery.yaml (delivery); demo-offline.yaml,
                  my-agency.yaml (outbound); templates/ (generic, recruitment, saas-funding,
                  local-business, agency-outreach)
examples/data/    made-up sample jobs, companies, contacts and replies for the offline demos
tests/            test files per module / area; fakes.py (FakeHttp, FakeLLM), conftest.py
                  (make_ctx fixture, TODAY = 2026-09-24; mode outbound unless mode="delivery")
                  - no test calls a real provider: adapters are tested against canned
                  payloads in the providers' documented response shapes
```

`leadgen adapters` prints every registered type per kind (source, finder,
verifier, llm, writer, exporter, notifier): offline / network / network (paid),
the credential it needs, and notes (use-at-own-risk, outbound mode only).

## The run: `Pipeline(ctx, out_dir, limit, hooks).run() -> RunResult`

Every stage is defensive: a broken source, a missing key or one bad company
is logged into `RunResult.errors` and the run carries on with what it has.
`BudgetExceeded` stops the paid work of a stage and becomes a warning
(`RunResult.warnings`).

| # | Stage | What happens | Key settings |
|---|---|---|---|
| 1 | **Source** | `collect()`: each enabled source's `fetch()`; `merge_companies` dedupes by domain, then by normalised name (same name + different domains = different companies). | `sources` |
| 2 | **Signals** | `process_signals`: keep accepted, fresh, matching signals; record them in the store (first seen / re-posted); drop undated / re-posted primary signals when configured; reject companies left without one. | `signals.*` |
| 3 | **ICP filter** | `apply_icp`: suppression list (domain, company name), excluded domains / names / keywords, location, size, industry. First failure is the reason in `rejected.csv`. | `icp.*` |
| 3b | **Hooks** | `hooks.filter_company(company)` may strip signals or return a reason to drop the company (stage `delivery` in `rejected.csv`). Runs before any paid lookup. | - |
| 4 | **Pre-score** | Score every company without a contact, best first, to spend enrichment credits on the best accounts. `--limit N` keeps the top N. | `enrichment.max_companies`, `min_prescore` |
| 5 | **Enrich** | `ContactWaterfall.enrich`: finders run in order until a target buyer with a usable email (or email guesses) exists; `select_contacts` ranks people by `buyers.titles`. Contacts that are suppressed (email / LinkedIn) or rejected by `hooks.filter_contact` are skipped. | `enrichment.finders`, `buyers.*` |
| 6 | **Verify** | `verify_contact`: checks up to 3 addresses (the provider's first, then the guesses); `valid` wins at once. A guess that wins is marked `contact.data["email_guessed"]`. Results other than `unknown` are cached for 30 days. When the budget is used up, a paid verifier is swapped for `basic`. `hooks.filter_contact` runs again once the email is known. | `enrichment.verifier` |
| 7 | **Score** | `score` + `tier_for`: intent / fit / reachability / extra, tier hot / normal / skip; notes explain gaps. | `scoring.*` |
| 8 | **Write** | *Outbound mode only.* For leads in `writer.tiers` with `email_ok` and no `block_reason`, one per email address per run (best score wins), up to `writer.max_leads`: `build_writer(ctx).write(lead)`. | `writer.*` |
| - | Save | `store.save_lead` for every lead (stages never move backwards). | |
| 9 | **Export** | Outbound mode: hand-over exporters (`scope = "outbound"`) run first on `outbound_leads(leads)`; what they hand over is marked EXPORTED (not in a dry run). Both modes: review exporters (`scope = "all"`: csv, json, gsheets) get every lead. | `outbound.*` |
| - | Finish | `rejected.csv` + `summary.json` in `<out_dir>/<run id>/`, `store.finish_run`, `RunResult.usage = ctx.usage.summary_lines()`, `notify("run_summary")`. | `notify.*` |

### The hand-over rules (outbound mode; pipeline methods)

* `email_ok(contact) -> bool`: the contact has an email whose status is in
  `enrichment.accept_statuses`. A **guessed** address must also be in
  `enrichment.accept_guessed_statuses` (default `[valid]`). In delivery mode
  the same test only decides which person is picked for the file.
* `block_reason(lead, company_state) -> Optional[str]`: why a lead must not be
  handed over (None = it may be). Shared by WRITE and EXPORT. Checked in order:
  1. `outbound.require_email` and not `email_ok` -> "no deliverable email";
  2. anyone at the company (same playbook) reached REPLIED, POSITIVE, BOOKED,
     WON or LOST (a lead lost only to a bounce is `lost_bounce` and does NOT
     block colleagues) -> "company already engaged (replied / lost) - not re-contacted";
  3. the email or the company domain is suppressed;
  4. the email is at an `icp.exclude_domains` domain;
  5. this lead was already handed over (`store.was_exported`);
  6. the same email was handed over (any playbook) within `outbound.dedupe_days`;
  7. the company was handed over within `outbound.company_cooldown_days` (default 30).
* `outbound_leads(leads) -> List[Lead]`: leads in `outbound.tiers` that have
  messages and no `block_reason`, one per email address (best score first).

### Dry run (`--dry-run`, `ctx.dry_run = True`)

Only adapters with `offline = True` run (csv/json sources, csv/pattern finders,
basic verifier, template writer, file exporters, console). A network verifier
falls back to `basic`; the AI writer, AI opening lines and the AI reply classifier fall back
to templates / rules. Outbound: upload files are written as `*.dry-run.csv`, API
exporters push nothing and nobody is recorded as handed over. Delivery: files
are named `...-PREVIEW.<ext>`, nothing is recorded in the ledger, and there is no
Google Sheets push. Slack / webhook alerts are only logged.

## The delivery layer (`leadgen/delivery/`)

The binding contract is [`docs/DELIVERY.md`](DELIVERY.md). In short,
`leadgen deliver --client NAME` calls `run.deliver(client, store=..., ...)`:

1. `client.load_client` reads `clients/NAME.yaml` (every problem listed at once,
   typos get a "did you mean"). `client.client_playbook` merges the client's
   targeting over its base playbook: `roles` -> `signals.match_keywords`,
   `locations` / `company_size` / `industries` -> `icp.*`, `exclusions` ->
   `icp.exclude_*` + client suppression, `freshness_days` / `allow_undated` /
   `drop_reposts` -> `signals.*`, `budget` -> `usage.max_paid_lookups`,
   `branding` -> `delivery.*`, then `overrides` last. It also forces
   `mode: delivery`, sets `name: client-<name>` and `buyers.max_contacts_per_company: 1`,
   caps `enrichment.max_companies`, and removes hand-over exporters.
2. `Context(..., usage=UsageMeter.from_playbook(pb, budget))` and
   `Pipeline(ctx, out_dir=<folder>/_internal, hooks=ledger.LedgerHooks(...)).run()`.
   `LedgerHooks` removes companies / jobs / contacts already delivered to this client
   (`dedupe`, `redelivery_days`) and everything on the client's do-not-list, before
   enrichment. It counts what it removed in `.removed`.
3. `run.select_leads`: tiers in `client.tiers`, a live job posting (or another
   primary signal), one row per company and per person, hot first then score, cut
   to `leads_per_week`. The rest are "held back" (not recorded).
4. `rows.build_row` per lead, with the client's email policy. `rows.email_label`
   gives exactly one of `verified` / `risky` / `guessed-unverified` / `not found`,
   and `rows.is_guessed` (pattern source, `email_guessed`, or a candidate address)
   always wins over a checker's `valid`. `opening.add_opening_lines` fills the
   optional column.
5. `formats.write_all` writes CSV / XLSX / HTML (plus `push_google_sheet`, not in dry runs).
6. `Ledger.record(client, lead_items(lead), run_id, today)` for every delivered row
   (not in dry runs).
7. `qa.build_qa` -> `_internal/qa.txt` + `qa.json` (+ `not_delivered.csv`).

A folder that already holds a delivery for that client and date is never
overwritten: the next one goes to `<folder>-2`, `-3`, ...

## Usage metering and the paid-lookup budget (`leadgen/usage.py`)

* `Context.usage` is a `UsageMeter`, built by `UsageMeter.from_playbook(pb, budget)`.
  The cap is `budget` (the `--budget` flag), else `usage.max_paid_lookups`. For a
  client delivery, `client_playbook` has already copied `budget.max_paid_lookups`
  into `usage`. `0` = no cap.
* `Adapter.http` wraps `ctx.http` in `MeteredHttp(inner, meter, adapter_kind, type_name, paid)`.
  `registry.create` sets `adapter.adapter_kind`, `type_name` and `paid =
  registry.is_paid(kind, type)` (the built-in `registry.PAID` types plus plugins
  registered with `paid=True`). Every request is counted per adapter. A request
  by a paid adapter is a **paid lookup**. Once the cap is reached,
  `before_request` raises `BudgetExceeded` **before** the network is touched.
  Free adapters keep working.
* The pipeline turns `BudgetExceeded` into warnings: sources and enrichment stop,
  and the verifier falls back to `basic`. The delivery QA shows one combined warning.
* LLM clients report tokens with `usage.record_llm(kind, type, model, in, out)`
  and expose `last_usage`. Credits-reporting providers call `usage.note_credits`.
  Prices come from `usage.cost_per_call` (per paid request, set by the user),
  `usage.llm_price_per_mtok` or the built-in `LLM_PRICES` (unknown models get a
  deliberately high fallback, so cost caps err on the safe side).
* `summary_lines()` is printed after every `run` and `deliver`: paid lookups vs
  cap, calls / tokens / credits per adapter, estimated cost.
* `leadgen doctor` sends its checks through the un-metered `ctx.http`, so they are
  never paid lookups and are never blocked by a budget.

## Live readiness (`leadgen/doctor.py`)

`run_doctor(ctx, google_sheet=None) -> List[DoctorResult]` builds every enabled
source / finder / verifier / exporter / notifier (plus the writer's AI model when
`writer.provider` is set) and checks each credential with **one free call**: an
account, credits, health or model-list endpoint (the table is in the module
docstring; `doctor_url` overrides one). `DoctorResult(kind, type, label, status,
detail, quota)` with `status` `ok` | `failed` | `missing_key` | `skipped`. It never
raises. Identical requests are made once. Every detail is redacted, and the keys
used are masked literally. A dry run only checks that keys are present.
`CHECKS[(kind, type)]` maps each adapter type to its check function, so plugins
can add their own.

## Risk flags (`registry.risk_note`)

Adapters that scrape sites whose terms forbid scraping are flagged "use at own
risk": the `linkedin_jobs` source, and the `apify` source with a LinkedIn / Indeed
preset or actor. `leadgen adapters` shows the note, `leadgen validate` warns, the
doctor shows it, and no shipped default playbook uses them.

## Store (`leadgen/store.py`)

One SQLite file (`storage.path`, default `data/leadgen.db`). Several playbooks
can share it. Rows are tagged with the playbook name, except the signal history,
the verification cache and the global suppression list, which are shared.
The delivery ledger lives in the same file (`deliveries` and `client_suppression`
tables, created on first use by `delivery.ledger.Ledger`).

| Area | Methods |
|---|---|
| Runs | `start_run(playbook, meta) -> run_id`, `finish_run(run_id, counts)`, `list_runs`, `latest_run_id` |
| Signal history | `observe_signals(company_key, signals, today)` - sets `first_seen`, `reposted` |
| Verification cache | `get_verification(email, max_age_days=30, today)`, `put_verification(email, status, provider)` |
| Leads | `save_lead(lead)` (stage never moves backwards; LOST is sticky), `get_lead`, `find_lead_by_email`, `find_leads_by_domain`, `leads_for_run`, `list_leads`, `set_stage(lead_id, stage, note, force)` (forward-only unless `force` or LOST) |
| Hand-over | `mark_exported(lead_id, exporter)`, `was_exported(lead_id)`, `recently_contacted(email, days, today, exclude_lead_id)`, `company_engagement(company_key, playbook, exclude_run_id="") -> {"stages": set, "last_exported": date or None}` |
| Suppression | `suppress(value, kind, reason)` with kinds `email` / `domain` / `company` / `linkedin` (`SUPPRESSION_KINDS`, values normalised by `normalize_suppression`), `unsuppress(value, kind=None) -> removed count`, `is_suppressed(email, domain, company, linkedin)` (an email is also blocked by its domain), `list_suppressed` |
| Replies | `save_reply(reply, playbook) -> id`, `find_reply(playbook, from_email, received_at) -> List[Reply]`, `list_replies` |
| Follow-ups | `schedule_followup(due, reason, lead_id, email, playbook)`, `due_followups(today, playbook=None, include_suppressed=False)`, `complete_followup(id)` |
| Stats | `funnel(playbook, since)` - cumulative stage counts, lost, tiers, replies by category |
| Ledger (`delivery/ledger.py`) | `Ledger(store)`: `delivered(client, kind, key, window_days, today)`, `record(client, items, run_id, today)`, `summary(client)`, `list_clients_with_history()`, `suppress` / `unsuppress` / `is_suppressed` / `list_suppressed` per client |

## Adapter contracts

Every adapter subclasses `Adapter` and is built as `Class(config: dict, ctx: Context)`,
normally through `registry.create(kind, config, ctx)`.

* **Class attributes:** `name` (the registry type), `env_key` (default env var
  of its key, e.g. `APOLLO_API_KEY`), `offline = True` only if it never uses
  the network. Set by `registry.create`: `adapter_kind`, `type_name`, `paid`.
* **Credentials:** `self.secret(config_key="api_key", env_key=None, required=True)`
  resolves config value > the env var named by config `<key>_env` > `env_key`.
  A missing key raises `MissingCredentialError` **lazily at first use**, never
  in `__init__`, so `leadgen validate` / `doctor` can build every adapter.
* **Network:** only through `self.http` (metered, see above); parse responses
  defensively (`.get`, `utils.get_path`). Respect `limit` / page caps.
* **Secrets never leak:** `HttpError` messages and logged URLs go through
  `redact` / `safe_url` (query keys, bearer tokens and token-like URL path
  segments are masked); pipeline errors are redacted before they are stored,
  and the webhook server masks `?token=`.
* **`leadgen validate` hooks (optional):** `required_credentials() -> [(config_key, env_var)]`
  and `validate()` / `check_config()` (raise to report a config problem).
* **No `print`:** use `self.log`. Every playbook entry may carry `enabled: false`
  (honoured for sources, finders and exporters) and `label`.

| Kind | Base (module) | Contract |
|---|---|---|
| source | `Source` (`sources/base.py`) | `fetch() -> List[Company]` with their `Signal`s (and `Contact`s if known); must not filter by ICP or score; `label`, `limit` (0 = all). |
| finder | `ContactFinder` (`enrich/base.py`) | `find(company) -> List[Contact]` (people without email are fine; `email_candidates` = guesses to verify); optional `complete(company, contact) -> Contact`. |
| verifier | `Verifier` (`verify/base.py`) | `verify(email) -> VerificationResult(email, status, raw_status, provider, detail)`; `status` is valid / risky / invalid / unknown; never raises for a bad address. |
| llm | `LLMClient` (`llm/base.py`) | `complete(system, user, json_mode=False, max_tokens=1500, temperature=None) -> str`, `complete_json(...) -> dict`; sets `last_usage` and records tokens in `ctx.usage`. Errors: `LLMError`, `LLMConfigError` (permanent), `LLMTruncatedError` (hit `max_tokens`). Built by `llm.build_llm(ctx)` from `writer.provider/model/base_url/temperature/api_key_env` plus the `writer.llm` options. |
| writer | `Writer` (`writer/base.py`) | *Outbound.* `write(lead) -> WriterOutput(messages, personalization, hypothesis, writer, warnings)`, one `Message` per `writer.sequence` step. `writer.build_writer(ctx)` picks `ai` only when `writer.type == "ai"`, an LLM is available and it is not a dry run; else `template`. |
| exporter | `Exporter` (`outbound/base.py`) | `export(leads, out_dir) -> ExportResult(exporter, count, path, detail, exported_ids)`. `scope = "outbound"` (hand-over, outbound mode only): gets only `outbound_leads` and what it returns counts as handed over; `scope = "all"`: review files, both modes. |
| notifier | `Notifier` (`notify/base.py`) | `send(event, title, text, data)`. `notify(ctx, event, ...)` sends to every channel when `event` is in `notify.on`; a channel's own `events: [...]` narrows it. Never raises. |

### Writers in more detail (outbound)

* **template** (offline): niche-neutral copy built from the offer, the top
  signal and the contact; `writer.templates` overrides subjects / steps.
  Guardrail problems become warnings.
* **ai**: one LLM call per lead returns the whole sequence as JSON (budget
  `writer.max_tokens`, doubled once if the answer was cut off). The copy is
  cleaned, signed by code, and checked by `guardrails.check_sequence` (length,
  banned phrases, placeholders, links, spammy wording, SHOUTED words minus
  known acronyms and `writer.acronyms`, must mention the company or signal).
  Problems -> one retry with feedback -> template fallback
  (`writer.fallback_to_template`). A missing / rejected key, or
  `writer.max_llm_failures` failures in a row, switches the AI off for the rest
  of the run.

### Replies (outbound; `leadgen/replies.py`, `leadgen/server.py`)

`handle_reply(reply, ctx) -> Reply` (calls `require_outbound` first): skip if
already stored (`find_reply`) -> `classify` (`replies.classifier`: rules | ai |
auto) -> link to a lead -> act:

| Category | Action |
|---|---|
| positive | REPLIED -> POSITIVE, alert with a draft answer + booking link |
| question / other | REPLIED (automated delivery notices change nothing) |
| referral | REPLIED; when an address was given, that person is saved as a new lead (unless suppressed or already a lead) for you to contact |
| timing | REPLIED; follow-up on the date given, else in `replies.timing_default_days` (30) |
| ooo | no stage change; follow-up the day after the return date, else in `replies.ooo_default_days` (7) |
| negative | REPLIED -> LOST, address suppressed, pending follow-ups cancelled |
| unsubscribe | LOST, address suppressed, pending follow-ups cancelled |
| bounce | the bounced address's lead -> LOST, address suppressed and cached as invalid |

Every category is also a `notify` event. Input comes from `leadgen replies
--file` (`load_replies_csv`) or from `leadgen serve`
(`make_server(ctx, host, port, token)`: `GET /health`, `POST /webhook`,
`/webhook/reply`, `/webhook/instantly`, `/webhook/smartlead`;
`parse_webhook_payload` detects Instantly / Smartlead / generic payloads).

## Configuration and environment

* Playbook and client values may use `${VAR}` / `${VAR:-default}`. `leadgen`
  loads `.env` (or `--env-file PATH`) first, and variables already set in the
  shell win.
* `.env.example` lists every variable the code or the shipped playbooks read,
  grouped delivery-first. `tests/test_e2e_delivery.py` checks that it stays complete.
* `LEADGEN_PLUGINS=module_a,module_b` imports those modules before any
  command, so they can `registry.register(kind, type, "module:Class")` their
  own adapters. Plugin types are metered; one registered with
  `register(kind, type, target, paid=True)` is a paid lookup capped by `--budget`
  (`registry.is_paid` = `registry.PAID` plus those plugins).
* CLI exit codes: 0 ok, 1 the command ran but found a problem (e.g. `deliver`
  delivered nothing, `doctor` found a bad key), 2 usage / configuration error
  (one friendly line on stderr; traceback with `-v`).
