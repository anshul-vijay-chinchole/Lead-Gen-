# Delivery layer - module contracts

The business: sell a weekly **Hiring Signal Report** to recruitment / staffing
agencies. We deliver lead files; we never email or run outreach for clients.
`mode: delivery` (the engine default) guarantees the pipeline stops after
scoring + export (see `leadgen/modes.py`, `leadgen/pipeline.py`).

Already written (binding): `leadgen/delivery/rows.py` (columns, `email_label`,
`build_row`, `DeliveryPackage`), `leadgen/pipeline.py` (`PipelineHooks`,
`Pipeline(ctx, out_dir, limit, hooks)`, `RunResult.warnings/usage/rejected`),
`leadgen/usage.py` (`UsageMeter`, `BudgetExceeded`, `LLM_PRICES`),
`leadgen/modes.py`, `leadgen/store.py` (suppression kinds email / domain /
company / linkedin), `leadgen/registry.py` (`PAID`, `risk_note`).

## Client file: `clients/<name>.yaml`

```yaml
display_name: Acme Staffing Ltd          # shown in reports (default: the file name)
playbook: playbooks/recruitment-delivery.yaml   # base settings (sources, finders, verifier, scoring)
contact: {name: Sam Lee, email: sam@acme-staffing.example}   # the client's contact (your records only)

roles: [accountant, controller, financial analyst]   # job titles they fill -> signals.match_keywords
exclude_roles: [intern, trainee]                      # -> signals.exclude_keywords (added to the playbook's)
buyer_titles: [CFO, VP Finance, Controller, Head of Talent]   # optional -> buyers.titles (replaces)
locations: [Texas, Oklahoma]                          # -> icp.locations (replaces); [] = anywhere
exclude_locations: []                                 # -> icp.exclude_locations
company_size: {min: 20, max: 1000}                    # -> icp.employees
industries: []                                        # -> icp.industries
exclusions:                                           # this client's own do-not-list
  companies: [Their Existing Client Inc]              # -> client suppression (company)
  domains: [bigclient.com]                            # -> icp.exclude_domains (added)
  keywords: [staffing, recruiting]                    # -> icp.exclude_keywords (added)

leads_per_week: 25                # target volume; the file holds at most this many
freshness_days: 7                 # only jobs posted in the last N days (-> signals.max_age_days)
allow_undated: false              # -> signals.allow_undated
drop_reposts: true                # -> signals.drop_reposts
redelivery_days: null             # null = never deliver the same item twice; N = allowed again after N days
dedupe: [company, job, contact]   # what the ledger dedupes on (remove 'company' to allow new jobs at known companies)
tiers: [hot, normal]              # which urgency tiers may be delivered
emails:
  include_unverified: true        # false: only "verified" emails are delivered (others -> "not found")
opening_line:
  enabled: false                  # adds the "Suggested opening line" column
  ai: false                       # false = free template line; true = AI (uses the playbook's writer provider/model)
  max_cost_usd: 0.50              # per-run cap for AI lines; over the cap -> template line
budget:
  max_paid_lookups: 0             # 0 = no cap (the --budget flag overrides)
delivery:
  formats: [csv, xlsx, html]      # any of csv, xlsx, html
  folder: deliveries/{client}/{date}   # {client}, {date} (YYYY-MM-DD) placeholders
  google_sheet: {spreadsheet_id: "", worksheet: "{date}"}   # optional push (needs gspread + service account)
branding: {}                      # optional overrides of the playbook's delivery.* branding
overrides: {}                     # advanced: any playbook section, deep-merged last
```

## Modules

### `delivery/client.py`
* `class ClientError(ValueError)`
* `@dataclass Client` with the fields above (typed, defaults as shown) plus
  `name` (file stem), `path`. `Client.email_policy_include_unverified -> bool`.
* `load_client(name_or_path: str, clients_dir: str = "clients") -> Client` -
  accepts `acme`, `acme.yaml` or a path; validates (clear `ClientError`
  messages listing every problem; unknown top-level keys are an error to catch typos).
* `list_clients(clients_dir="clients") -> List[str]` (names; skips files starting with `_`).
* `client_playbook(client: Client, env=None) -> Playbook` - loads `client.playbook`
  (relative to the CWD, then the client file's folder), applies the mapping above,
  forces `mode: delivery`, sets `name` to `client-<name>` (sanitised),
  `enrichment.max_companies` to `max(leads_per_week * 2, 10)` unless the base or
  overrides set it lower, `buyers.max_contacts_per_company: 1`, `usage.max_paid_lookups`
  from `budget`, removes hand-over exporters, merges `branding` into `delivery`,
  then deep-merges `overrides`, and validates via `playbook.from_dict`.
* `new_client_file(name, clients_dir="clients", template="clients/_template.yaml") -> Path`
  (refuses to overwrite; name must match `^[a-z0-9][a-z0-9_-]*$`).

### `delivery/ledger.py`
Tables in the same SQLite file as the `Store` (created on first use, via `store.conn`):
`deliveries(client, kind, key, run_id, delivered_at, PRIMARY KEY(client, kind, key))`,
`client_suppression(client, kind, value, reason, added_at, PRIMARY KEY(client, kind, value))`.
* `company_item_key(company) -> str` (= `company.key`)
* `job_item_key(company, signal) -> str` - `"<company.key>|<external_id>"`, else the
  URL without tracking parameters (utm_*, gh_src, lever-source, ...; parameters that
  identify the job such as Indeed `jk` / Greenhouse `gh_jid` are kept), else
  `<company.key>|<signal fingerprint>`
* `contact_item_key(contact) -> str` - email, else `linkedin_key(url)` (any country host,
  nothing after `/in/<name>`), else `name|company`
* `lead_items(lead)` - the (kind, key) pairs recorded for a delivered lead. For a company
  with a domain the same items are also recorded under "by name" bookkeeping kinds, so
  the company is recognised when a later source lists it without a website.
* `class Ledger(store)`:
  `delivered(client, kind, key, window_days: Optional[int], today) -> bool`
  (window None = ever; N = within the last N days);
  `record(client, items: Iterable[Tuple[kind, key]], run_id, today) -> int`;
  `summary(client) -> dict` (`deliveries` - from the append-only `delivery_runs` table -,
  `first_delivery`, `last_delivery`, counts per kind);
  `list_clients_with_history() -> List[str]`;
  `suppress(client, value, kind, reason="")`, `unsuppress(client, value, kind=None) -> int`,
  `is_suppressed(client, email="", domain="", company="", linkedin="") -> bool`,
  `list_suppressed(client) -> List[dict]`. Kinds: email, domain, company, linkedin
  (normalised with `store.normalize_suppression`).
* `class LedgerHooks(PipelineHooks)` built from `(ledger, client, today)`:
  `filter_company` drops the company if its key was delivered (when `company` in
  `client.dedupe`), if the client suppresses its domain / name, and strips job
  signals whose `job_item_key` was delivered (when `job` in dedupe); a company
  left with no signal -> reason "all its jobs were already delivered".
  `filter_contact` rejects contacts delivered before (when `contact` in dedupe) or
  suppressed for the client (email / linkedin). `filter_enriched` (after enrichment,
  before verification) drops a company without a website whose people turn out to be
  at a domain on the client's do-not-list (`company_do_not_list` also checks the
  company LinkedIn page and the provider's `email_domain` hint). Counts what it removed
  in `.removed = {"company": n, "job": n, "contact": n, "suppressed": n}`. (The pipeline
  applies the same after-enrichment check to the global suppression list and
  `icp.exclude_domains`.)

### `delivery/opening.py`
* `template_line(row: dict) -> str` - free, factual, no AI: e.g.
  `"Saw Acme is hiring a Senior Accountant in Austin, TX (posted 2 days ago)."`
* `add_opening_lines(rows, leads_by_id, ctx, client) -> OpeningStats` - fills
  `row["opening_line"]`: template lines unless `client.opening_line.ai`; with AI,
  uses `ctx.llm` (the playbook writer provider/model), one short call per row,
  and stops using AI (template for the rest) once the next call could push the
  estimated spend over `max_cost_usd` (pre-check with a worst-case estimate:
  prompt chars/4 input tokens + max_tokens output at `ctx.usage.price_for(model)`;
  actual cost from `llm.last_usage` when present). Never raises; LLM errors ->
  template line. `OpeningStats(ai_lines, template_lines, cost_usd, capped: bool, errors: int)`.

### `delivery/formats.py`
* `write_csv(pkg, path) -> Path` - UTF-8 with BOM (Excel-friendly), header row =
  column headers, formula-injection guard (cells starting with = + - @ get a `'`).
* `write_xlsx(pkg, path) -> Path` - openpyxl: sheet "Leads" (bold coloured
  header, frozen header row, autofilter, sensible column widths, clickable links
  for Website / Job link / LinkedIn URL, dates as dates), sheet "About" (client,
  period, counts by signal and by email status, the email-status legend).
* `write_html(pkg, path, top=10) -> Path` - branded one-pager (brand name /
  colour / logo from `pkg.brand`): header, KPI tiles (leads delivered, hot,
  verified emails, companies), counts by signal type, a table of the top 10
  hottest leads (company, role(s), posted, location, urgency, decision-maker,
  email status), footer with sender + website. Self-contained HTML, inline CSS,
  print-friendly, every value `html.escape`d, works with 0 rows.
* `push_google_sheet(pkg, cfg: dict, ctx) -> str` - gspread (lazy import; clear
  `RuntimeError` if missing), service account from `cfg.service_account_file` or
  `GOOGLE_APPLICATION_CREDENTIALS`; worksheet name `cfg.worksheet` with `{date}` /
  `{client}`; a `{date}` tab that already holds data is never overwritten (rows go to
  `<tab>-2`, `-3`, ...), a fixed name is replaced; written without clearing first, so a
  failed push leaves the tab unchanged; returns the sheet URL. Not called in dry runs
  or for an empty delivery.
* `write_all(pkg, folder, formats) -> Dict[str, Path]` - file names
  `<client>-hiring-signals-<YYYY-MM-DD>.<ext>`.

### `delivery/qa.py`
* `@dataclass QAReport` with: `client`, `leads_found` (companies sourced),
  `with_signal`, `qualified`, `delivered`, `target`, `filtered_out` (total),
  `top_reasons: List[Tuple[str, int]]` (grouped: details in parentheses and
  numbers stripped, e.g. "too small", "no signal matching [...] in last 7 days"),
  `duplicates_removed` (ledger company/job/contact + same company/contact within the
  run), `suppressed`, `verified_email_rate` (verified / delivered, 0..1),
  `email_status_counts`, `warnings: List[str]` (volume below target, budget
  reached, source errors, 0 leads, AI cost cap hit), `usage_lines`.
  `.lines() -> List[str]` (printed after each run) and `.to_dict()`.
* `build_qa(client, run_result, pkg, ledger_removed, within_run_dupes, opening_stats=None, usage=None) -> QAReport`.

### `delivery/run.py`
* `@dataclass DeliveryResult(client, files: Dict[str, Path], sheet_url, package, qa, run, dry_run)`
* `deliver(client, *, store, env=None, http=None, today=None, dry_run=False,
  budget=None, out_dir=None, log=None) -> DeliveryResult`:
  1. `client_playbook(client)`; `Context(playbook, http, store, env, today, dry_run,
     usage=UsageMeter.from_playbook(pb, budget))`.
  2. `Pipeline(ctx, out_dir=<folder>/_internal, hooks=LedgerHooks(...)).run()`.
  3. Leads with a tier in `client.tiers`; one row per company (best score) and per
     contact; sorted hot first then score; cut to `leads_per_week`.
  4. `build_row(...)` with the email policy; opening lines if enabled.
  5. `DeliveryPackage` -> `write_all`; optional Google Sheets push (not in dry run).
  6. Ledger: record company / job / contact keys of what was delivered (not in dry run;
     dry-run files are named `...-PREVIEW.<ext>`).
  7. QA report -> `<folder>/_internal/qa.txt` + `qa.json`; returned.

### `leadgen/doctor.py`
* `@dataclass DoctorResult(kind, type, label, status, detail, quota="")` - status
  `ok` | `failed` | `missing_key` | `skipped` (no live check available / not needed).
* `run_doctor(ctx) -> List[DoctorResult]` - every enabled source / finder /
  verifier / exporter / notifier in the playbook plus the writer LLM when a
  provider is set. One minimal FREE call per credential (account / credits /
  model-list endpoints); never a paid lookup. Free no-key adapters: `skipped`
  ("no key needed") unless a cheap reachability check exists.

## CLI (added)
* `leadgen deliver --client NAME [--clients-dir clients] [--dry-run] [--budget N] [--out DIR] [--db PATH]`
* `leadgen clients [list]` / `leadgen clients new NAME`
* `leadgen doctor (-p PLAYBOOK | --client NAME)`
* `leadgen suppress add|remove|list VALUE [--kind email|domain|company|linkedin] [--client NAME]`
* `--budget N` on `run` and `deliver`; usage + estimated cost printed after both.
* `replies`, `serve`, `followups`: in delivery mode print the outbound-only
  message and exit 2 without doing anything.
