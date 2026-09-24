# Architecture & module contracts

How the engine is put together, and the promises each part makes to the
others. The code is the source of truth: every module starts with a docstring
that lists the playbook keys it reads, and `leadgen/playbook.py` `DEFAULTS`
holds every setting with its default (`playbooks/templates/generic.yaml`
explains each one in plain English).

The engine is **niche-agnostic** and **country-agnostic**: nothing about a
niche, a country or a legal regime is hard-coded. Everything that changes
between niches lives in a playbook (YAML).

## Module layout

```
leadgen/
  cli.py          `leadgen <command>`: init, validate, run, demo, leads, replies, serve,
                  stats, mark, suppress, followups, adapters. main(argv) -> exit code
  playbook.py     Playbook, DEFAULTS, load_playbook / from_dict (merge over defaults,
                  expand ${ENV} / ${ENV:-default}, validate)
  context.py      Context (playbook, http, store, env, today, log, dry_run, llm) and the
                  Adapter base class (secret(), has_secret(), offline)
  registry.py     playbook `type:` name -> "module:Class", imported lazily; register()
  http.py         HttpClient (retry/backoff on 429 + 5xx), HttpError, redact(), safe_url()
  models.py       Signal, Contact, Company, Lead, Message, ScoreBreakdown, Reply +
                  vocabularies SignalType, EmailStatus, Tier, Stage, ReplyCategory
  utils.py        normalisers (text, domain, company name, company_key), parse_date,
                  to_int, is_valid_email, is_personal_email, contains_any, get_path, ...
  store.py        SQLite Store - the engine's memory between runs
  pipeline.py     Pipeline(ctx).run() - one full run, stage by stage
  signals.py      stage 2: process_signals, signal_stats, keyword_match (the one matcher)
  filters.py      stage 3: apply_icp, check_icp, fit_checks, excluded_domain
  contacts.py     stage 5: ContactWaterfall, select_contacts, title_rank, is_generic_email
  scoring.py      stage 7: score, tier_for
  replies.py      classify + act on replies (rules / ai / auto)
  report.py       demo one-pager (write_demo), funnel_report, runs_report
  server.py       webhook receiver for reply events (make_server)
  sources/        base, csv_source (csv, json), mapping (column detection + helpers),
                  theirstack, adzuna, apollo, apify, ats (greenhouse, lever, ashby)
  enrich/         base, csv_finder, apollo, hunter, pattern          (contact finders)
  verify/         base, basic, millionverifier, zerobounce, neverbounce, hunter
  llm/            base, openai (openai + openai_compatible), anthropic
  writer/         base, template, ai, prompts, guardrails
  outbound/       base, csv_export (csv, json, upload-CSV base), instantly (instantly_csv,
                  instantly), smartlead (smartlead_csv, smartlead), gsheets, webhook
  notify/         base, console, slack, webhook + the notify() dispatcher
tests/            test files per module / area; fakes.py (FakeHttp, FakeLLM), conftest.py
                  (make_ctx fixture, TODAY = 2026-09-24) - no test calls a real provider:
                  adapters are tested against canned payloads in the providers'
                  documented response shapes
playbooks/        demo-offline.yaml, my-agency.yaml, templates/ (generic, recruitment,
                  saas-funding, local-business, agency-outreach)
examples/data/    made-up sample companies, contacts and replies for the offline demo
```

`leadgen adapters` prints every registered type per kind (source, finder,
verifier, llm, writer, exporter, notifier) with the credential it needs.

## The run: `Pipeline(ctx).run() -> RunResult`

Every stage is defensive: a broken source, a missing key or one bad company
is logged into `RunResult.errors` and the run carries on with what it has.

| # | Stage | What happens | Key settings |
|---|---|---|---|
| 1 | **Source** | `collect()`: each enabled source's `fetch()`; `merge_companies` dedupes by domain, then by normalised name (same name + different domains = different companies). | `sources` |
| 2 | **Signals** | `process_signals`: keep accepted, fresh, matching signals; record them in the store (first seen / re-posted); reject companies left without one. | `signals.*` |
| 3 | **ICP filter** | `apply_icp`: suppression list, excluded domains / names / keywords, location, size, industry. First failure is the reason in `rejected.csv`. | `icp.*` |
| 4 | **Pre-score** | Score every company without a contact, best first, to spend enrichment credits on the best accounts. `--limit N` keeps the top N. | `enrichment.max_companies`, `min_prescore` |
| 5 | **Enrich** | `ContactWaterfall.enrich`: finders run in order until a target buyer with a usable email (or email guesses) exists; `select_contacts` ranks people by `buyers.titles`. | `enrichment.finders`, `buyers.*` |
| 6 | **Verify** | `verify_contact`: checks up to 3 addresses (the provider's first, then the guesses); `valid` wins at once. Results other than `unknown` are cached for 30 days. Suppressed addresses are skipped. Contacts are verified only until `buyers.max_contacts_per_company` deliverable ones are found. | `enrichment.verifier` |
| 7 | **Score** | `score` + `tier_for`: intent / fit / reachability / extra, tier hot / normal / skip; notes explain gaps ("guessed email only risky (needs valid)"). | `scoring.*` |
| 8 | **Write** | For leads in `writer.tiers` with `email_ok` and no `block_reason`, one per email address per run (best score wins), up to `writer.max_leads`: `build_writer(ctx).write(lead)`. Leads that could never be handed over are not written, so no AI money is spent on them. | `writer.*` |
| - | Save | `store.save_lead` for every lead (stages never move backwards). | |
| 9 | **Export** | Hand-over exporters (`scope = "outbound"`) run first on `outbound_leads(leads)`; what they hand over is marked EXPORTED via `store.mark_exported` (not in a dry run). Then review exporters (`scope = "all"`) get every lead with its final stage. | `outbound.*` |
| - | Finish | `rejected.csv` + `summary.json` in `output/<playbook>/<run id>/`, `store.finish_run`, `notify("run_summary")`. | `notify.*` |

### The hand-over rules (pipeline methods)

* `email_ok(contact) -> bool` - the contact has an email whose status is in
  `enrichment.accept_statuses`; a **guessed** address (the verifier picked
  one of the pattern finder's candidates, marked `contact.data["email_guessed"]`)
  must also be in `enrichment.accept_guessed_statuses` (default `[valid]`).
* `block_reason(lead, company_state) -> Optional[str]` - why a lead must not be
  handed over (None = it may be). Shared by WRITE and EXPORT. Checked in order:
  1. `outbound.require_email` and not `email_ok` -> "no deliverable email";
  2. anyone at the company (same playbook) reached REPLIED, POSITIVE, BOOKED,
     WON or LOST (a "no", an unsubscribe or `leadgen mark ... lost`; a lead lost
     only to a bounce is reported as `lost_bounce` and does NOT block colleagues -
     the company cooldown still applies) -> "company already engaged (replied / lost)
     - not re-contacted";
  3. the email or the company domain is suppressed;
  4. the email is at an `icp.exclude_domains` domain;
  5. this lead was already handed over (`store.was_exported`) - so a person is
     handed over at most once per playbook;
  6. the same email was handed over (any playbook) within `outbound.dedupe_days`;
  7. the company was handed over within `outbound.company_cooldown_days` (default 30).

  `company_state` caches `store.company_engagement` per company for one pass.
* `outbound_leads(leads) -> List[Lead]` - leads in `outbound.tiers` that have
  messages and no `block_reason`, one per email address (best score first).

### Dry run (`leadgen run --dry-run`, `ctx.dry_run = True`)

Only adapters with `offline = True` run (csv/json sources, csv/pattern finders,
basic verifier, template writer, file exporters, console). A network verifier
falls back to `basic`; the AI writer and AI reply classifier fall back to
templates / rules. Hand-over upload files are written as
`instantly_upload.dry-run.csv` / `smartlead_upload.dry-run.csv`, API exporters
push nothing, Slack / webhook alerts are only logged, and nobody is recorded
as handed over.

## Store (`leadgen/store.py`)

One SQLite file (`storage.path`, default `data/leadgen.db`); several playbooks
can share it - rows are tagged with the playbook name, except the signal
history, the verification cache and the suppression list, which are shared.

| Area | Methods |
|---|---|
| Runs | `start_run(playbook, meta) -> run_id`, `finish_run(run_id, counts)`, `list_runs`, `latest_run_id` |
| Signal history | `observe_signals(company_key, signals, today)` - sets `first_seen`, `reposted` |
| Verification cache | `get_verification(email, max_age_days=30, today)`, `put_verification(email, status, provider)` |
| Leads | `save_lead(lead)` (stage never moves backwards; LOST is sticky), `get_lead`, `find_lead_by_email`, `find_leads_by_domain`, `leads_for_run`, `list_leads`, `set_stage(lead_id, stage, note, force)` (forward-only unless `force` or LOST) |
| Hand-over | `mark_exported(lead_id, exporter)`, `was_exported(lead_id)`, `recently_contacted(email, days, today, exclude_lead_id)`, `company_engagement(company_key, playbook, exclude_run_id="") -> {"stages": set, "last_exported": date or None}` (ignores this run's own leads that were not handed over) |
| Suppression | `suppress(value, kind="email"/"domain", reason)`, `unsuppress(value, kind=None) -> removed count` (kind defaults to email when the value has an "@"), `is_suppressed(email, domain)` (an email is also blocked by its domain), `list_suppressed` |
| Replies | `save_reply(reply, playbook) -> id`, `find_reply(playbook, from_email, received_at) -> List[Reply]` (duplicate check for webhook retries / CSV re-imports), `list_replies` |
| Follow-ups | `schedule_followup(due, reason, lead_id, email, playbook)`, `due_followups(today, playbook=None, include_suppressed=False)` (people suppressed after scheduling are hidden unless `include_suppressed`), `complete_followup(id)` |
| Stats | `funnel(playbook, since)` - cumulative stage counts, lost, tiers, replies by category |

## Adapter contracts

Every adapter subclasses `Adapter` and is built as `Class(config: dict, ctx: Context)`.

* **Class attributes:** `name` (the registry type), `env_key` (default env var
  of its key, e.g. `APOLLO_API_KEY`), `offline = True` only if it never uses
  the network.
* **Credentials:** `self.secret(config_key="api_key", env_key=None, required=True)`
  resolves config value > the env var named by config `<key>_env` > `env_key`.
  A missing key raises `MissingCredentialError` **lazily at first use**, never
  in `__init__`, so `leadgen validate` can build every adapter.
* **Network:** only through `self.http` (`ctx.http`, an `HttpClient`); parse
  responses defensively (`.get`, `utils.get_path`). Respect `limit` / page caps.
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
| llm | `LLMClient` (`llm/base.py`) | `complete(system, user, json_mode=False, max_tokens=1500, temperature=None) -> str`, `complete_json(...) -> dict`. Errors: `LLMError`, `LLMConfigError` (permanent), `LLMTruncatedError` (hit `max_tokens`). Built by `llm.build_llm(ctx)` from `writer.provider/model/base_url/temperature/api_key_env` plus the `writer.llm` options. |
| writer | `Writer` (`writer/base.py`) | `write(lead) -> WriterOutput(messages, personalization, hypothesis, writer, warnings)`, one `Message` per `writer.sequence` step. `writer.build_writer(ctx)` picks `ai` only when `writer.type == "ai"`, an LLM is available and it is not a dry run; else `template`. |
| exporter | `Exporter` (`outbound/base.py`) | `export(leads, out_dir) -> ExportResult(exporter, count, path, detail, exported_ids)`. `scope = "outbound"`: gets only `outbound_leads` and what it returns counts as handed over (upload CSVs included); `scope = "all"`: review files. |
| notifier | `Notifier` (`notify/base.py`) | `send(event, title, text, data)`. `notify(ctx, event, ...)` sends to every channel when `event` is in `notify.on`; a channel's own `events: [...]` narrows it. Never raises. |

### Writers in more detail

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

### Replies (`leadgen/replies.py`, `leadgen/server.py`)

`handle_reply(reply, ctx) -> Reply`: skip if already stored (`find_reply`) ->
`classify` (`replies.classifier`: rules | ai | auto) -> link to a lead -> act:

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

* Playbook values may use `${VAR}` / `${VAR:-default}`; `leadgen` loads `.env`
  (or `--env-file PATH`) first, and variables already set in the shell win.
* `.env.example` lists every variable the code or the shipped playbooks read.
* `LEADGEN_PLUGINS=module_a,module_b` imports those modules before any
  command, so they can `registry.register(kind, type, "module:Class")` their
  own adapters.
* CLI exit codes: 0 ok, 1 the command ran but found a problem, 2 usage /
  configuration error (one friendly line on stderr; traceback with `-v`).
