# leadgen

**A signal-led lead generation engine that works for any niche.** It finds companies
that have a reason to buy *right now*, works out who decides, finds and checks their
email, scores every lead from 0 to 100, writes a short personal email sequence, hands
the leads to your sending tool, and sorts the replies.

It has no niche built in. A recruitment agency, a SaaS tool, a dental marketing service
and your own agency all run the same engine. Each one gets its own **playbook**, a
settings file.

---

## How it works, in plain English

Think of a factory line. Companies go in at one end, each station does one job, and
people who want to talk come out at the other.

| # | Station | What happens |
|---|---|---|
| 1 | **Find** | Pull in companies showing a *buying signal*: hiring, just raised money, expanding, new boss, bad reviews. |
| 2 | **Filter** | Throw out anything that isn't your ideal customer: wrong size, wrong industry, competitors, existing clients. |
| 3 | **Find the person** | Work out *who* decides (e.g. VP Finance, founder, practice manager). |
| 4 | **Get + check the email** | Find their work email and verify it, so emails don't bounce and hurt your sender reputation. |
| 5 | **Score** | 0-100 based on how hot the signal is, how well they fit and whether we can reach them. **hot / normal / skip**. |
| 6 | **Write** | A short email plus follow-ups that say *why them, why now*, written by AI or from free templates. |
| 7 | **Send** | Upload files (or a direct push) for Instantly / Smartlead, which send slowly from warmed-up inboxes. |
| 8 | **Read replies** | Sort replies into interested / question / wrong person / not now / not interested / unsubscribe / out-of-office / bounce. |
| 9 | **Hand off** | Interested replies alert you (terminal, Slack, webhook) with a draft answer and your booking link. |
| 10 | **Report** | Funnel numbers: found, verified, sent, replied, interested, booked. Plus a one-page "live opportunities" report to win clients. |

The engine remembers everything in a small database file (SQLite), so it never hands the
same person to your sending tool twice, never emails anyone who asked to be removed,
and notices when a job has been open for weeks or was re-posted.

### What is a playbook?

A playbook is the recipe card for one niche. It's a YAML file with plain settings:

- **offer**: who you are, what you sell, your call to action and booking link
- **icp**: your ideal customer (size, industries, places, competitors to skip)
- **signals**: which "why now" signals count and how fresh they must be
- **buyers**: which job titles to email, best first
- **sources / enrichment / outbound**: which tools to use at each station
- **scoring / writer / replies / notify**: how to score, write, sort replies and alert

A new niche means a new playbook. No code changes. Every template in
[`playbooks/templates/`](playbooks/templates) is commented line by line.

---

## Quickstart (5 minutes, no API keys)

Requires Python 3.9+.

```bash
git clone <this repo> && cd Lead-Gen-
pip install -e .                       # installs the `leadgen` command

leadgen run -p playbooks/demo-offline.yaml
```

The demo playbook runs **100% offline** on made-up sample data: a fictional recruitment
agency, *Northbeam Talent*, looking for companies hiring finance and engineering people.
You'll see a run summary like this:

```
  sourced companies ....... 18
  with a live signal ...... 15
  match the ICP ........... 11
  decision-maker found .... 9
  hot / normal / skip ..... 3 / 4 / 4
  sequences written ....... 7
  handed to outbound ...... 7
```

Everything lands in `output/demo-offline/<run id>/`:

| File | What it is |
|---|---|
| `opportunities.csv` | **Every** lead: score, tier, reasons, the signal, the person, their email + status, and the full email sequence. Open it in Excel or import it into Google Sheets to review. |
| `instantly_upload.csv` | Hot + normal leads with a checked email, in Instantly's import format (email, name, company, `subject_1`, `email_1..4` as custom variables). |
| `smartlead_upload.csv` | The same for Smartlead. |
| `leads.json` | Everything, for other tools. |
| `rejected.csv` | Every company that was filtered out **and why** (e.g. "too large (25000 employees, max 1000)", "no signal matching [...]: 1 excluded by keyword (intern)"). |
| `summary.json` | Run counts + top 20 leads. |

Then try the rest of the loop:

```bash
leadgen leads -p playbooks/demo-offline.yaml            # the leads as a table
leadgen demo  -p playbooks/demo-offline.yaml --prospect "Northbeam Talent"
#   -> demo-northbeam-talent.md + .html: a one-page "live opportunities" report
leadgen replies -p playbooks/demo-offline.yaml --file examples/data/demo_replies.csv
#   -> classifies 12 sample replies, moves leads to positive/lost, suppresses
#      unsubscribes + bounces, schedules follow-ups, alerts on interested replies
leadgen stats -p playbooks/demo-offline.yaml            # the funnel
leadgen run   -p playbooks/demo-offline.yaml            # run again: nobody is handed over twice
```

The demo database is `data/demo.db`. Delete it to start fresh.

---

## Going live

1. **Copy the key file:** `cp .env.example .env` and fill in the keys your playbook uses.
   `.env` is read from the folder you run `leadgen` in. Variables already set in your
   shell always win over the file.
2. **Check:** `leadgen validate -p playbooks/my-agency.yaml` builds every adapter the
   playbook uses and prints a checklist. Unknown adapter types, missing files, missing
   config (like an Instantly `campaign_id`) and missing API keys show as `[FAIL]`.
3. **Rehearse for free:** `leadgen run -p ... --dry-run` skips everything that touches
   the network. No paid calls, nothing sent.
4. **Small paid test:** `leadgen run -p ... --limit 10` enriches only the 10 best companies.
5. **Full run.** Review `opportunities.csv`, then upload `instantly_upload.csv` (or switch
   on the `instantly` / `smartlead` exporter to push straight into a campaign).

### Which key does what

Only the adapters named in your playbook need keys. `leadgen adapters` lists every type.

| Adapter (`type:`) | Kind | Env var(s) | What it's for |
|---|---|---|---|
| `csv`, `json` | source | none | Your own files or any export (Apollo, Clay, Sales Navigator, Apify downloads). Columns auto-detected. |
| `theirstack` | source | `THEIRSTACK_API_KEY` | Job postings with company data and hiring teams. |
| `adzuna` | source | `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` | Job postings by country. |
| `apollo` | source | `APOLLO_API_KEY` | Company search with funding and headcount-growth signals. |
| `apify` | source | `APIFY_TOKEN` | Any Apify scraper; presets for Google Maps, LinkedIn Jobs, Indeed. |
| `greenhouse`, `lever`, `ashby` | source | none | Open jobs of a watchlist of companies (public ATS boards). |
| `apollo` | finder | `APOLLO_API_KEY` | People search + email reveal. |
| `hunter` | finder | `HUNTER_API_KEY` | Domain search + email finder; learns the company's email pattern. |
| `csv` | finder | none | People you already have in a file. |
| `pattern` | finder | none | Free: guesses `first.last@`, `flast@`, ... for the verifier to test. |
| `basic` | verifier | none | Free, offline: syntax + disposable domains (can't confirm an address). |
| `millionverifier` / `zerobounce` / `neverbounce` / `hunter` | verifier | `MILLIONVERIFIER_API_KEY` / `ZEROBOUNCE_API_KEY` / `NEVERBOUNCE_API_KEY` / `HUNTER_API_KEY` | Real email verification: valid / risky (catch-all) / invalid. |
| writer `provider: anthropic` | llm | `ANTHROPIC_API_KEY` | AI-written emails and AI reply classification. |
| writer `provider: openai` | llm | `OPENAI_API_KEY` | Same, with OpenAI. |
| writer `provider: openai_compatible` | llm | the var named in `writer.api_key_env` | OpenRouter, Groq, a local server, ... |
| `csv`, `json`, `instantly_csv`, `smartlead_csv` | exporter | none | Files in the run folder. |
| `instantly` | exporter | `INSTANTLY_API_KEY` (+ `campaign_id`) | Push leads into an Instantly campaign. |
| `smartlead` | exporter | `SMARTLEAD_API_KEY` (+ `campaign_id`) | Push leads into a Smartlead campaign. |
| `gsheets` | exporter | `GOOGLE_APPLICATION_CREDENTIALS` or `GOOGLE_SERVICE_ACCOUNT_JSON` | Write the review sheet to Google Sheets (`pip install gspread`). |
| `webhook` | exporter | `LEADGEN_EXPORT_WEBHOOK_URL` | POST finished leads to Zapier / Make / n8n / a CRM. |
| `console` | notifier | none | Alerts in the terminal. |
| `slack` | notifier | `SLACK_WEBHOOK_URL` | Alerts in Slack. |
| `webhook` | notifier | `LEADGEN_WEBHOOK_URL` | Alerts to any URL. |
| `leadgen serve` | server | `LEADGEN_WEBHOOK_TOKEN` | Shared secret for the reply webhook. |

Playbooks can also use your own variables: `${SENDER_NAME}` or `${SENDER_NAME:-default}`
anywhere in the YAML is replaced from the environment / `.env`.

---

## Your own playbook

```bash
leadgen init acme-recruiting --template recruitment    # -> playbooks/acme-recruiting.yaml
# edit it: offer, icp, signals.match_keywords, buyers.titles, sources
leadgen validate -p playbooks/acme-recruiting.yaml
leadgen run -p playbooks/acme-recruiting.yaml --dry-run
```

`init` never overwrites an existing file. Templates:

| Template | Finds | Emails | Tools |
|---|---|---|---|
| `generic` | reference for **every** setting; runs offline on the sample data | - | csv (+ disabled examples of paid tools) |
| `recruitment` | companies hiring the roles an agency fills | hiring managers | TheirStack + Adzuna, Apollo -> Hunter -> pattern, MillionVerifier, AI writer |
| `saas-funding` | recently funded B2B software companies | founders, heads of growth | Apollo, MillionVerifier, AI writer |
| `local-business` | local businesses on Google Maps (rating / review signals) | owners, practice managers | Apify Google Maps, Hunter, MillionVerifier |
| `agency-outreach` | B2B service firms hiring sales / BD people (they need pipeline) | founders, MDs, heads of BD | TheirStack, Apollo -> Hunter -> pattern, MillionVerifier, AI writer |

[`playbooks/my-agency.yaml`](playbooks/my-agency.yaml) is `agency-outreach` set up for
finding clients for your own lead-gen / AI-automation agency. Your name, company and
booking link come from `.env`.

**Tips**

- The niche lives in three places: `sources` (what to search for), `signals.match_keywords`
  (which signals count) and `buyers.titles` (who to email, best first).
- `rejected.csv` tells you exactly why companies were dropped. Use it to tune the ICP.
- `enrichment.max_companies` and `--limit` cap paid lookups per run.
- Put existing clients in `icp.exclude_domains` and competitors in `icp.exclude_keywords`.

---

## How scoring works

Every lead gets 0-100 from four parts. The weights are set in the playbook (`scoring.weights`).

| Part | Default max | Earned by |
|---|---|---|
| **Intent** | 40 | The *primary* signal (e.g. a matching job post): **fresh** (full points within 3 days, then 7 / 14 / 30-day bands), **volume** (3+ matching signals = full), **persistence** (re-posted, or still open after `stale_after_days` = a hard-to-fill need), **urgency** ("urgent", "asap", "immediate start" ...). |
| **Fit** | 30 | Location, size and industry each match (full), are unknown (half, `unknown_credit`) or don't match (none). |
| **Reachability** | 20 | Half for the person (top buyer title 100%, #2-3 80%, lower 60%, non-target title 30%), half for the email (valid 100%, catch-all 50%, unverified 30%). |
| **Extra** | 10 | +5 per bonus signal type (funding, expansion, leadership change, ...). |

`hot` >= 80, `normal` >= 60, otherwise `skip` (`scoring.tiers`). Only hot and normal leads get
emails written and are handed to sending tools (`writer.tiers`, `outbound.tiers`). Every lead
carries its reasons, e.g. `fresh signal (1d); 3 matching signals; open 26d; urgent language;
size match; VP of Finance = buyer #2; email valid; +funding`, so you can see why it scored
the way it did.

---

## Daily automation

Run it every morning with cron (Linux/macOS), from the repo folder so `.env` and the
relative paths are found:

```cron
# m h dom mon dow  command
0 7 * * 1-5  cd /path/to/Lead-Gen- && mkdir -p logs && /path/to/venv/bin/leadgen run -p playbooks/my-agency.yaml >> logs/leadgen.log 2>&1
```

The database remembers what was already handed over, so a daily run only ever adds new
people. `leadgen run` exits with code 1 only when every source failed, which is what you
want to alert on.

**GitHub Actions:** a scheduled workflow (`on: schedule: - cron: "0 7 * * 1-5"`) that runs
`pip install -e .` and `leadgen run ...` works too. Store your keys as repository secrets
and pass them as `env:`. Keep the database between runs, e.g. by committing
`data/leadgen.db` to a private branch or storing it as an artifact/cache. Without the
database the dedupe memory is lost.

---

## Handling replies

Replies are classified into `positive`, `question`, `referral`, `timing`, `negative`,
`unsubscribe`, `ooo` (out of office), `bounce` or `other`, and acted on:

| Category | What happens |
|---|---|
| positive | lead -> **positive**; alert with a draft answer + your booking link |
| question | lead -> replied; alert with a draft answer |
| referral | lead -> replied; a **new lead** for the person they pointed you to; alert |
| timing ("not now, try next quarter") | lead -> replied; follow-up scheduled on the date they gave |
| ooo | follow-up scheduled the day after they're back |
| negative | lead -> lost; address suppressed |
| unsubscribe | lead -> lost; address suppressed |
| bounce | lead -> lost; bounced address suppressed and marked invalid |

`replies.classifier`: `rules` (free keyword rules), `ai` (your writer's AI model) or `auto`
(AI when configured; bounces / out-of-office / unsubscribes always by rules).
`leadgen followups` lists follow-ups that are due; `leadgen followups --done <id>` ticks one off.

### Option 1: CSV import

Export replies from your sending tool (columns like `from`/`from_email`, `subject`, `body`,
`received_at`) and run:

```bash
leadgen replies -p playbooks/my-agency.yaml --file replies.csv
```

It prints a table and writes `replies_classified.csv`. Importing the same file twice is safe.

### Option 2: webhook server (real time)

```bash
leadgen serve -p playbooks/my-agency.yaml --port 8787 --token "$(openssl rand -hex 16)"
# or put LEADGEN_WEBHOOK_TOKEN=... in .env
```

| Endpoint | |
|---|---|
| `GET /health` | `{"ok": true}` for uptime checks |
| `POST /webhook/instantly` | Instantly "reply received" events |
| `POST /webhook/smartlead` | Smartlead "email reply" events |
| `POST /webhook`, `/webhook/reply` | generic `{from_email, subject, body, received_at}` |

The server listens on `127.0.0.1` only. To receive webhooks from Instantly / Smartlead,
put it behind HTTPS (a reverse proxy, or a tunnel such as Cloudflare Tunnel or ngrok) and
**always set a token**. Most tools can only set the URL, so add the token as a query
parameter:

- **Instantly:** in the webhook settings, add
  `https://your-host/webhook/instantly?token=YOUR_TOKEN` for the *reply received* event
  (bounce and unsubscribe events are understood too).
- **Smartlead:** in the campaign (or account) webhook settings, add
  `https://your-host/webhook/smartlead?token=YOUR_TOKEN` for *Email Reply*.

Menu names change from time to time. Look for "Webhooks" in each tool's settings.

Other events (sent, opened, clicked) are acknowledged and ignored. Requests without the
right token get `401`, bad JSON `400`, bodies over 1 MB `413`. Retried deliveries are
recognised and not processed twice.

### Manual updates

```bash
leadgen mark --email jane@acme.com --stage booked         # booked, won, lost, positive, ...
leadgen suppress add competitor.com --reason competitor    # a domain or an email
leadgen suppress add --file do-not-contact.csv             # many at once (TXT or CSV)
leadgen suppress list
```

---

## Commands

Global options work on every command, before or after it: `-p/--playbook PATH`,
`--db PATH` (override `storage.path`), `-v` / `-vv` (more logging + tracebacks),
`--env-file PATH` (default `.env`).

| Command | What it does |
|---|---|
| `leadgen init NAME [--template T] [--dir playbooks]` | New playbook from a template (never overwrites). |
| `leadgen validate -p PB [--dry-run]` | Checklist of adapters, config and API keys. Exit 1 on problems. |
| `leadgen run -p PB [--out DIR] [--limit N] [--dry-run]` | One full run. Exit 1 only if every source failed. |
| `leadgen demo -p PB [--run ID\|latest] [--top 5] [--prospect NAME] [--no-mask] [--out DIR]` | "Live opportunities" report (Markdown + HTML). |
| `leadgen leads [-p PB] [--run ID\|latest\|all] [--tier hot\|normal\|skip] [--limit 20]` | Leads as a table. |
| `leadgen replies -p PB --file CSV [--out DIR]` | Classify + act on replies; writes `replies_classified.csv`. |
| `leadgen serve -p PB [--host 127.0.0.1] [--port 8787] [--token T]` | Reply webhook server. |
| `leadgen stats [-p PB] [--since YYYY-MM-DD]` | Funnel + recent runs. |
| `leadgen mark (--email E \| --lead ID) --stage STAGE [--note N] [--force]` | Move a lead by hand (forward only unless `--force`). |
| `leadgen suppress add\|remove\|list [VALUE] [--kind email\|domain] [--reason R] [--file F]` | Do-not-contact list. |
| `leadgen followups [--days N] [--done ID]` | Due follow-ups. |
| `leadgen adapters` | Every adapter type, whether it's offline, and its key. |

`python -m leadgen ...` works the same as `leadgen ...`. Configuration errors (bad
playbook, missing file or key) print one `error:` line and exit with 2.

---

## Extending: your own adapter

Every station is a plug-in. An adapter is a small class with `__init__(config, ctx)`
(inherited) and one method:

| Kind | Base class | Method |
|---|---|---|
| source | `leadgen.sources.base.Source` | `fetch() -> List[Company]` (with `Signal`s) |
| finder | `leadgen.enrich.base.ContactFinder` | `find(company) -> List[Contact]`, optional `complete(company, contact)` |
| verifier | `leadgen.verify.base.Verifier` | `verify(email) -> VerificationResult` |
| exporter | `leadgen.outbound.base.Exporter` | `export(leads, out_dir) -> ExportResult` |
| notifier | `leadgen.notify.base.Notifier` | `send(event, title, text, data)` |

```python
# my_plugins.py
from leadgen import registry
from leadgen.models import Company, Signal
from leadgen.sources.base import Source


class TradeShowSource(Source):
    """Exhibitors of a trade show (from the show's public JSON)."""

    name = "tradeshow"
    env_key = "TRADESHOW_API_KEY"   # read lazily via self.secret()

    def fetch(self):
        data = self.http.get_json(self.config["url"], headers={"Authorization": self.secret()})
        return [Company(name=x.get("name", ""), website=x.get("website", ""), sources=[self.label],
                        signals=[Signal(type="event", title=f"Exhibiting at {self.config['show']}",
                                        source=self.label)])
                for x in data.get("exhibitors", [])][: self.limit or None]


registry.register("source", "tradeshow", "my_plugins:TradeShowSource")
```

Then set `LEADGEN_PLUGINS=my_plugins` (in `.env` or the environment) and use
`{type: tradeshow, url: ..., show: ...}` in a playbook. `leadgen adapters` and `leadgen
validate` will list it. Rules every adapter follows:

- All HTTP goes through `self.http`, which retries 429/5xx and redacts keys from logs. Never import `requests` in an adapter.
- Read keys with `self.secret()` at first use, never in `__init__`.
- Set `offline = True` only if the adapter never touches the network.
- Parse responses defensively (`.get(...)`).

Tests use `tests.fakes.FakeHttp` with canned responses (see `tests/test_sources_apis.py`).
Run the suite with `pip install -e ".[dev]" && pytest -q`.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `error: playbook not found` | Run from the repo folder, or pass the full path to `-p`. |
| `[FAIL] ... missing credential: set $X` | Add `X=...` to `.env` (in the folder you run from) or export it. Check with `leadgen validate`. |
| `sourced companies ... 0` in a dry run | Network sources are skipped in `--dry-run`. Use a csv source to rehearse, or run for real. |
| Everything ends up in `rejected.csv` | Read the reasons. Usually `signals.match_keywords` is too narrow, `max_age_days` too short, or the ICP size/industry too strict. |
| Leads have no email | Add finders (`apollo` -> `hunter` -> `pattern`) and a verifier. `pattern` guesses need a real verifier to become "valid". |
| No emails written | Only `hot`/`normal` leads with an accepted email status get sequences (`enrichment.accept_statuses`, `writer.tiers`). |
| The AI writer isn't used | `leadgen validate` shows why (missing key, provider). A dry run always uses the free template writer. |
| The same people aren't exported again | That's the dedupe (`outbound.dedupe_days`). Use a different `--db` to start fresh. |
| A webhook returns 401 | The token in the URL (`?token=`) or the `X-Leadgen-Token` header doesn't match `--token` / `LEADGEN_WEBHOOK_TOKEN`. |
| Need details | Add `-v` (info) or `-vv` (debug + full tracebacks). |

---

## Project layout

```
leadgen/
  cli.py            command line (leadgen ...)
  server.py         reply webhook server (leadgen serve)
  pipeline.py       the factory line: source -> signals -> ICP -> enrich -> verify -> score -> write -> export
  playbook.py       playbook loading + every default setting (DEFAULTS)
  models.py         Company, Signal, Contact, Lead, Message, Reply, ...
  store.py          SQLite memory: runs, signal history, leads + stages, suppression, replies, follow-ups
  signals.py        which signals count, freshness, reposts
  filters.py        ICP filter (size, industry, location, exclusions)
  contacts.py       buyer-title ranking + the finder waterfall
  scoring.py        0-100 score + tiers
  replies.py        reply cleaning, classification and actions
  report.py         demo report, funnel + run stats
  registry.py       adapter type -> class
  http.py           shared HTTP client (retries, key redaction)
  sources/          csv/json, theirstack, adzuna, apollo, apify, greenhouse/lever/ashby
  enrich/           apollo, hunter, csv, pattern finders
  verify/           basic, millionverifier, zerobounce, neverbounce, hunter
  llm/              anthropic, openai (+ compatible)
  writer/           template writer, AI writer, prompts, guardrails
  outbound/         csv/json, instantly, smartlead, google sheets, webhook
  notify/           console, slack, webhook
playbooks/
  demo-offline.yaml offline demo (no keys)
  my-agency.yaml    finding clients for your own agency
  templates/        generic (every setting), recruitment, saas-funding, local-business, agency-outreach
examples/data/      fictional sample signals, contacts and replies
tests/              pytest suite (no network: HTTP is faked)
docs/ARCHITECTURE.md  module contracts for contributors
```
