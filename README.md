# leadgen

**A signal-led lead generation engine that works for any niche.** It finds companies
that have a reason to buy *right now*, works out who decides, finds and checks their
email, scores every lead from 0 to 100, writes a short personal email sequence, hands
the leads to your sending tool, and sorts the replies.

It has no niche built in. A recruitment agency, a SaaS tool, a dental marketing service
and your own agency all run the same engine. Each one gets its own **playbook**, a
settings file.

leadgen never sends an email itself. It prepares the leads and their emails; your
sending tool (Instantly, Smartlead, ...) does the sending.

**Contents:** [How it works](#how-it-works-in-plain-english) ·
[Quickstart (offline demo)](#quickstart-the-offline-demo-5-minutes-no-api-keys) ·
[Going live](#going-live) · [Your own playbook](#your-own-playbook) ·
[Scoring](#how-scoring-works) · [Safety](#safety-who-never-gets-emailed) ·
[Daily automation](#daily-automation) · [Replies](#handling-replies) ·
[Commands](#commands) · [Extending](#extending-your-own-adapter) ·
[Troubleshooting](#troubleshooting)

---

## How it works, in plain English

Think of a factory line. Companies go in at one end, each station does one job, and
people who want to talk come out at the other.

| # | Station | What happens |
|---|---|---|
| 1 | **Find** | Pull in companies showing a *buying signal*: hiring, just raised money, expanding, new boss, bad reviews. |
| 2 | **Filter** | Throw out anything that isn't your ideal customer: no live signal, wrong size, wrong industry, competitors, existing clients. |
| 3 | **Find the person** | Work out *who* decides (e.g. VP Finance, founder, practice manager). |
| 4 | **Get + check the email** | Find their work email and verify it, so emails don't bounce and hurt your sender reputation. |
| 5 | **Score** | 0-100 based on how hot the signal is, how well they fit and whether we can reach them: **hot / normal / skip**. |
| 6 | **Write** | A short email plus follow-ups that say *why them, why now*, written by AI or from free templates. |
| 7 | **Hand over** | Upload files for Instantly / Smartlead (or a direct push into a campaign). Your sending tool sends slowly from warmed-up inboxes. |
| 8 | **Read replies** | Sort replies into interested / question / wrong person / not now / out-of-office / not interested / unsubscribe / bounce / other. |
| 9 | **Hand off** | Interested replies alert you (terminal, Slack, webhook) with a draft answer and your booking link. |
| 10 | **Report** | Funnel numbers: found, verified, handed over, replied, interested, booked, won. Plus a one-page "live opportunities" report to win clients. |

The engine remembers everything in a small database file (SQLite), so it never hands the
same person to your sending tool twice, never emails anyone who asked to be removed,
doesn't pester a company that already answered, and notices when a job has been open
for weeks or was re-posted.

### What is a playbook?

A playbook is the recipe card for one niche. It's a YAML file with plain settings:

- **offer**: who you are, what you sell, your call to action and booking link
- **icp**: your ideal customer (size, industries, places, competitors to skip)
- **signals**: which "why now" signals count and how fresh they must be
- **buyers**: which job titles to email, best first
- **sources / enrichment / outbound**: which tools to use at each station
- **scoring / writer / replies / notify**: how to score, write, sort replies and alert
- **storage**: where the database file lives

A new niche means a new playbook. No code changes. Every template in
[`playbooks/templates/`](playbooks/templates) is commented line by line, and
[`generic.yaml`](playbooks/templates/generic.yaml) lists every setting.

---

## Quickstart: the offline demo (5 minutes, no API keys)

You need Python 3.9 or newer and git.

```bash
git clone <this repo's URL> Lead-Gen-
cd Lead-Gen-
python3 -m venv .venv            # a private Python just for this project
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .                 # installs the `leadgen` command

leadgen run -p playbooks/demo-offline.yaml
```

Run every `leadgen` command from this folder, with the venv activated (your prompt
starts with `(.venv)`). Playbooks, sample data, `.env`, `data/` and `output/` are all
found relative to it.

The demo playbook runs **100% offline** on made-up sample data: a fictional recruitment
agency, *Northbeam Talent*, looking for companies hiring finance and engineering people.
You'll see a run summary like this:

```
  sourced companies ....... 18
  with a live signal ...... 15
  match the ICP ........... 11
  decision-maker found .... 9
  email verified .......... 9
  hot / normal / skip ..... 3 / 4 / 4
  sequences written ....... 7
  handed to outbound ...... 7
```

"Email verified" counts people whose email passed the playbook's rules (the demo
accepts unconfirmed addresses because no real checker runs offline). "Handed to
outbound" counts people written into the upload files; from now on they count as
contacted.

Everything lands in `output/demo-offline/<run id>/` (the run id looks like
`20260924-170919-1a33a0`):

| File | What it is |
|---|---|
| `opportunities.csv` | Every lead that passed the filters (hot, normal **and** skip): score, tier, stage, the reasons for the score, the signal, the person, their email + status, the full email sequence and notes (e.g. why a lead was not handed over). Open it in Excel or import it into Google Sheets to review. |
| `instantly_upload.csv` | The people handed over in this run, in Instantly's import format: `email, first_name, last_name, company_name, website, ...` then the custom variables `subject_1, email_1 .. email_4, signal, score, tier`. |
| `smartlead_upload.csv` | The same, in Smartlead's format. |
| `leads.json` | Every lead with all its data, for other tools. |
| `rejected.csv` | Every company that was filtered out **and why**, e.g. `too large (25000 employees, max 1000)` or `no signal matching [accountant, controller, ...] in last 60 days (1 signal found: 1 excluded by keyword (intern))`. |
| `summary.json` | Run counts, errors and the top 20 leads. |

Writing an upload file counts as handing those people over, even if you never import
it, so the next run won't include them again. Only hot and normal leads that may be
emailed end up in upload files (see [Safety](#safety-who-never-gets-emailed)).

Then try the rest of the loop:

```bash
leadgen leads -p playbooks/demo-offline.yaml            # the leads as a table
leadgen demo  -p playbooks/demo-offline.yaml --prospect "Northbeam Talent"
#   -> demo-northbeam-talent.md + .html in the run folder: a one-page
#      "live opportunities" report (emails masked; --no-mask shows them)
leadgen replies -p playbooks/demo-offline.yaml --file examples/data/demo_replies.csv
#   -> sorts 12 sample replies: moves leads to replied / positive / lost, suppresses
#      the unsubscribe, the "not interested" and the bounce, adds a lead for a
#      referral, schedules follow-ups and alerts on the interested, referral and
#      question replies.
#      Writes output/demo-offline/replies_classified.csv
leadgen followups -p playbooks/demo-offline.yaml --days 30   # follow-ups coming up
leadgen stats -p playbooks/demo-offline.yaml            # the funnel
leadgen run   -p playbooks/demo-offline.yaml            # run again: nobody is handed over twice
```

The second run shows `sequences written ... 0` and `handed to outbound ... 0`: everyone
was already handed over, and companies that replied are not contacted again. The
`notes` column of the new `opportunities.csv` says why for each lead.

The demo keeps its memory in `data/demo.db`. Delete that file (and `output/demo-offline/`
if you like) to start fresh.

> Commands that don't need a playbook (`leads`, `stats`, `mark`, `suppress`,
> `followups`) use `data/leadgen.db` when you leave out `-p`. For the demo, always add
> `-p playbooks/demo-offline.yaml` so they use `data/demo.db`.

---

## Going live

1. **Keys file:** `cp .env.example .env` (Windows: `copy .env.example .env`), then fill in
   your details (`SENDER_NAME`, `BOOKING_LINK`, ...) and the keys your playbook uses. Leave
   the lines you don't use empty. `.env` is read from the folder you run `leadgen` in;
   variables already set in your shell win over the file; `--env-file PATH` reads a
   different file. `.env` is in `.gitignore`: never commit it.
2. **Check:** `leadgen validate -p playbooks/my-agency.yaml` builds every adapter the
   playbook uses and prints a checklist. Unknown adapter types, missing files, missing
   config (like an Instantly `campaign_id`) and missing API keys show as `[FAIL]`, and
   the command exits with 1 until they are fixed.
3. **Rehearse for free:** `leadgen run -p playbooks/my-agency.yaml --dry-run`. Nothing that
   uses the network runs: no paid calls, nothing sent, nobody recorded as handed over.
   Only `csv` / `json` sources run, so a playbook whose sources are all APIs finds
   nothing; switch on a csv source (my-agency has one called `my-list`, `enabled: false`)
   to rehearse on your own file. Paid finders and verifiers are skipped too, so a
   rehearsal mostly tests your signal and ICP rules. Upload files are written as
   `*.dry-run.csv` (never import those) and the AI writer is replaced by the free
   template writer.
   `leadgen validate -p ... --dry-run` checks a playbook for this mode (missing keys
   become warnings).
4. **Small paid test:** `leadgen run -p playbooks/my-agency.yaml --limit 10`. Only the 10
   best companies (after filtering) are enriched, verified, scored, written and handed
   over. The sources still fetch their normal amount: cap those with each source's own
   settings (e.g. TheirStack `limit` / `max_pages`).
5. **Full run.** Review `opportunities.csv`, then import `instantly_upload.csv` into your
   campaign (or switch on the `instantly` / `smartlead` exporter to push straight into a
   campaign).

**Setting up the campaign.** leadgen writes the whole sequence per person; the campaign
only displays it. In Instantly or Smartlead, create one step per email and use the
variables from the upload file: step 1 subject `{{subject_1}}`, body `{{email_1}}`; step 2
empty subject (= same thread), body `{{email_2}}`; and so on. Match the timing to your
`writer.sequence` days (default: day 1, 3, 7 and 12). `{{personalization}}`, `{{signal}}`,
`{{job_title}}`, `{{score}}` and `{{tier}}` are available too. If line breaks disappear,
add `body_format: html` to the exporter.

### Which key does what

Only the adapters named in your playbook need keys. `leadgen adapters` lists every type
and its key.

| Adapter (`type:`) | Kind | Env var(s) | What it's for |
|---|---|---|---|
| `csv`, `json` | source | none | Your own files or any export (Apollo, Clay, Sales Navigator, Apify downloads). Columns auto-detected. |
| `theirstack` | source | `THEIRSTACK_API_KEY` | Job postings with company data and hiring teams. |
| `adzuna` | source | `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` | Job postings, one country code per search (`countries:` is required, e.g. `[us]` or `[gb, ca]`). |
| `apollo` | source | `APOLLO_API_KEY` | Company search with funding and headcount-growth signals. |
| `apify` | source | `APIFY_TOKEN` | Any Apify scraper; presets for Google Maps, LinkedIn Jobs, Indeed. |
| `greenhouse`, `lever`, `ashby` | source | none | Open jobs of a watchlist of companies (public job boards). |
| `apollo` | finder | `APOLLO_API_KEY` | People search + email reveal. |
| `hunter` | finder | `HUNTER_API_KEY` | Domain search + email finder; learns the company's email pattern. |
| `csv` | finder | none | People you already have in a file. |
| `pattern` | finder | none | Free: guesses `first.last@`, `flast@`, ... for people found without an email, for the verifier to test. |
| `basic` | verifier | none | Free, offline: syntax + disposable domains (can't confirm an address). |
| `millionverifier` / `zerobounce` / `neverbounce` / `hunter` | verifier | `MILLIONVERIFIER_API_KEY` / `ZEROBOUNCE_API_KEY` / `NEVERBOUNCE_API_KEY` / `HUNTER_API_KEY` | Real email verification: valid / risky (catch-all) / invalid. Results are cached for 30 days. |
| writer `provider: anthropic` | llm | `ANTHROPIC_API_KEY` | AI-written emails and AI reply sorting. Default model `claude-sonnet-5`; `claude-opus-5-5` (best, costs more) or `claude-haiku-4-5` (cheapest). |
| writer `provider: openai` | llm | `OPENAI_API_KEY` | Same, with OpenAI. Default model `gpt-5-mini`. The key is only ever sent to api.openai.com. |
| writer `provider: openai_compatible` | llm | the var named in `writer.api_key_env` | OpenRouter, Groq, a local server, ... Also needs `base_url` and `model`. |
| `csv`, `json`, `instantly_csv`, `smartlead_csv` | exporter | none | Files in the run folder. |
| `instantly` | exporter | `INSTANTLY_API_KEY` (+ `campaign_id`) | Push leads into an Instantly campaign. |
| `smartlead` | exporter | `SMARTLEAD_API_KEY` (+ `campaign_id`) | Push leads into a Smartlead campaign. |
| `gsheets` | exporter | `GOOGLE_APPLICATION_CREDENTIALS` or `GOOGLE_SERVICE_ACCOUNT_JSON` | Write the review sheet to Google Sheets (`pip install gspread` first). |
| `webhook` | exporter | `LEADGEN_EXPORT_WEBHOOK_URL` | POST the handed-over leads to Zapier / Make / n8n / a CRM. |
| `console` | notifier | none | Alerts in the terminal. |
| `slack` | notifier | `SLACK_WEBHOOK_URL` | Alerts in Slack. |
| `webhook` | notifier | `LEADGEN_WEBHOOK_URL` | Alerts to any URL. |
| `leadgen serve` | server | `LEADGEN_WEBHOOK_TOKEN` | Shared secret for the reply webhook. |

To read a key from a differently named variable, add `api_key_env: OTHER_NAME` to that
adapter's entry in the playbook (`leadgen validate` names the exact setting when a key is
missing).

Playbooks can use your own variables too: `${SENDER_NAME}` or `${SENDER_NAME:-default}`
anywhere in the YAML is replaced from the environment / `.env` (the text after `:-` is
used while the variable is empty or missing).

---

## Your own playbook

```bash
leadgen init acme-recruiting --template recruitment    # -> playbooks/acme-recruiting.yaml
# edit it: offer, icp, signals.match_keywords, buyers.titles, sources
leadgen validate -p playbooks/acme-recruiting.yaml
leadgen run -p playbooks/acme-recruiting.yaml --dry-run
```

`init` never overwrites an existing file (`--dir` picks another folder). Templates:

| Template | Finds | Emails | Tools |
|---|---|---|---|
| `generic` (default) | reference for **every** setting; runs offline on the sample data | founders, heads of operations | csv (+ disabled examples of paid tools), csv + pattern finders, basic verifier, template writer |
| `recruitment` | companies hiring the finance / accounting roles an agency fills | finance leaders, talent acquisition | TheirStack + Adzuna, Apollo -> Hunter -> pattern, MillionVerifier, AI writer |
| `saas-funding` | recently funded B2B software companies | founders, heads of growth / sales / marketing | Apollo, Apollo -> Hunter -> pattern, MillionVerifier, AI writer, Smartlead file |
| `local-business` | local businesses on Google Maps (rating / review signals) | owners, practice managers | Apify Google Maps, Hunter -> pattern, MillionVerifier, template writer |
| `agency-outreach` | B2B service firms hiring sales / BD people (they need pipeline) | founders, MDs, heads of BD | TheirStack, Apollo -> Hunter -> pattern, MillionVerifier, AI writer |

[`playbooks/my-agency.yaml`](playbooks/my-agency.yaml) is `agency-outreach` set up for
finding clients for your own lead-gen / AI-automation agency. Your name, company and
booking link come from `.env`.

### The settings you'll change most

Leave a setting out and its default is used. Defaults in brackets.

| Section | Setting | What it does |
|---|---|---|
| `offer` | `sender_name`, `sender_title`, `sender_company`, `sender_website` | Who signs the emails. |
| | `service`, `value_prop`, `proof` | What you do; `value_prop` completes "We help teams like *company* ..."; one real result. |
| | `cta` ["Worth a quick chat?"], `booking_link`, `footer` | Call to action, your calendar link, an optional line under every email. |
| `icp` | `locations`, `employees: {min, max}`, `industries` | Who fits. Empty = anyone. |
| | `exclude_industries`, `exclude_keywords`, `exclude_domains` | Competitors, existing clients (their email domains are blocked too). |
| | `unknown_passes` [true] | A company with missing size / location / industry data passes that check. |
| `signals` | `types` [all], `primary` [job_posting] | Which signals count; primary ones drive the score, the rest are bonus points. |
| | `match_keywords`, `exclude_keywords` | A primary signal's title must mention one (e.g. the roles you recruit for); drop titles with these (e.g. intern). |
| | `match_description` [true] | `true`: a keyword in the signal's description also counts; `false`: it must be in the title. |
| | `max_age_days` [60], `stale_after_days` [21] | Ignore older signals; still open after N days = hard-to-fill need (scores higher). |
| `buyers` | `titles` | Job titles to email, **best first**. |
| | `max_contacts_per_company` [1], `allow_generic_emails` [false] | People per company; allow info@ / jobs@ style inboxes. |
| `sources` | list of `{type: ..., ...}` | Where companies come from. Each takes `label` and `enabled: false`; the template comments show the rest. |
| `enrichment` | `finders`, `verifier` [basic] | Finders run in order until someone is found; then the email is checked. |
| | `accept_statuses` [valid, risky] | Which checker results may be emailed. |
| | `accept_guessed_statuses` [valid] | Stricter rule for guessed `first.last@` addresses (see Safety). |
| | `max_companies` [200] | Credit guard: only the best N companies per run are enriched. |
| `scoring` | `weights`, `tiers` [hot 80, normal 60] | See [How scoring works](#how-scoring-works). |
| `writer` | `type` [template], `provider`, `model` | `ai` + `anthropic` / `openai` / `openai_compatible` for AI copy. |
| | `max_words` [90], `sequence` [days 1 / 3 / 7 / 12], `tone`, `extra_instructions`, `banned_phrases` | Shape of the emails. |
| | `max_tokens` [4000] | Output budget per AI answer (newer models think inside it); a cut-off answer is retried once with double. |
| | `max_llm_failures` [3] | After this many AI failures in a row the template writer takes over for the rest of the run. |
| | `acronyms` [] | Extra ALL-CAPS words the copy checker allows (it flags shouted words of 5+ capitals). |
| | `llm` [{}] | Extra AI client options: `timeout` (seconds, default 120), `effort` (anthropic), `reasoning_effort` (openai), `extra_body`, `extra_headers`, `api_key_required: false` (local server without a key). |
| | `fallback_to_template` [true], `max_leads` [500], `tiers` [hot, normal] | If the AI fails use templates; cap on sequences per run; which tiers get emails. |
| `outbound` | `exporters` [csv], `tiers` [hot, normal] | Where finished leads go; which tiers are handed over. |
| | `dedupe_days` [90], `company_cooldown_days` [30] | See [Safety](#safety-who-never-gets-emailed). |
| `replies` | `classifier` [auto], `timing_default_days` [30], `ooo_default_days` [7] | See [Handling replies](#handling-replies). |
| `notify` | `channels` [console], `"on"` [positive, referral, question, run_summary] | Where alerts go and which events alert. Keep `"on"` in quotes. |
| `storage` | `path` [data/leadgen.db] | The database file. Several playbooks can share one. |

**Tips**

- The niche lives in three places: `sources` (what to search for), `signals.match_keywords`
  (which signals count) and `buyers.titles` (who to email, best first).
- `rejected.csv` tells you exactly why companies were dropped. Use it to tune the ICP.
- `enrichment.max_companies` and `--limit` cap paid lookups per run; `writer.max_leads`
  caps AI calls.
- Put existing clients in `icp.exclude_domains` and competitors in `icp.exclude_keywords`.

---

## How scoring works

Every lead gets 0-100 from four parts. The weights are set in the playbook (`scoring.weights`).

| Part | Default max | Earned by |
|---|---|---|
| **Intent** | 40 | The *primary* signal (e.g. a matching job post): **fresh** (full points within 3 days, then less at 7 / 14 / 30 days), **volume** (3+ matching signals = full), **persistence** (re-posted, or still open after `stale_after_days` = a hard-to-fill need), **urgency** ("urgent", "asap", "immediate" ...). |
| **Fit** | 30 | Location, size and industry each match or aren't set (full), are unknown (half, `unknown_credit`) or don't match (none). |
| **Reachability** | 20 | Half for the person (top buyer title 100%, #2-3 80%, lower 60%, a title not on the list 30%), half for the email (valid 100%, catch-all "risky" 50%, unverified 30%). |
| **Extra** | 10 | +5 per bonus signal type (funding, expansion, leadership change, ...). |

`hot` >= 80, `normal` >= 60, otherwise `skip` (`scoring.tiers`). Only hot and normal leads get
emails written and are handed to sending tools (`writer.tiers`, `outbound.tiers`). Every lead
carries its reasons in `opportunities.csv`, e.g. `fresh signal (1d); 3 matching signals;
open 26d; urgent language; size match; industry match; VP of Finance = buyer #2; email
valid; +funding`, so you can see why it scored the way it did.

---

## Safety: who never gets emailed

These rules are built in. The notes column of `opportunities.csv` says which one stopped a lead.

- **Nobody is handed over twice.** A person is handed over at most once per playbook,
  ever. The same email address coming in through another playbook or another company
  record in the same database is blocked for `outbound.dedupe_days` (90).
- **One email per address per run.** If one person turns up under two company records,
  only the higher-scored lead is written and handed over.
- **Suppression list.** Unsubscribes, "not interested" replies and bounces are added
  automatically; you can add emails or whole domains yourself (`leadgen suppress`).
  A company on a suppressed domain is filtered out before any paid lookup (it shows up
  in `rejected.csv`); a suppressed address is skipped before verification. Neither is
  ever handed over. The list lives in the database file, so every playbook that uses
  that file respects it.
- **Company cooldown.** After a playbook handed over someone at a company, it hands over
  no colleague there for `outbound.company_cooldown_days` (30; 0 = off). Once anyone at a
  company has replied, unsubscribed, said no or been marked lost, that playbook never hands
  over anyone else there. A bounce is different: it only means that address is dead, so
  the bounced address is suppressed and a colleague can be tried after the cooldown.
- **Guessed emails need proof.** Addresses guessed from a name pattern (`first.last@...`,
  built by the `pattern` finder) are only handed over when a real verifier says `valid`
  (`enrichment.accept_guessed_statuses`, default `[valid]`). So by default catch-all
  "risky" guesses, and guesses checked only by the free `basic` checker, are never sent.
  Addresses supplied by a data provider or your own file need a status in
  `enrichment.accept_statuses` (default `[valid, risky]`). (The offline demo loosens both
  rules because nothing can be confirmed offline; never copy that into a live playbook.)
- **Excluded domains.** Emails at a domain in `icp.exclude_domains` are never handed over.
- **No money spent on dead ends.** Emails are only written for leads that could actually be
  handed over, so the AI isn't paid to write to people the rules above would block.
- **Dry runs are rehearsals.** `--dry-run` makes no network calls (network sources, finders,
  pushes and Slack / webhook alerts are skipped; the free `basic` checker and the template writer stand in
  for the paid verifier and the AI), records nobody as handed over and writes the upload
  files as `instantly_upload.dry-run.csv` / `smartlead_upload.dry-run.csv`. Don't import
  those: only a real run records who was handed over. The run and its leads are still
  saved (marked `dry-run` in `leadgen stats`), so `leads` and `demo` work on them.
- **Secrets stay out of logs.** API keys, bearer tokens, webhook tokens and tokens inside
  URLs (e.g. Slack hook URLs) are masked in log lines and error messages, including the
  errors saved in `summary.json`.
- **Spreadsheet-safe files.** Text cells that start with `=`, `+`, `-` or `@` are
  prefixed with `'` so Excel / Google Sheets never run them as formulas.

leadgen has no country-specific or legal rules built in. You are responsible for
following the laws and your sending tool's terms wherever you and the people you email
are. `offer.footer`, the suppression list and the automatic unsubscribe handling are
there to help.

---

## Daily automation

Run it every morning with cron (Linux/macOS: `crontab -e`), from the repo folder so
`.env` and the relative paths are found:

```cron
# m h dom mon dow  command
0 7 * * 1-5  cd /path/to/Lead-Gen- && mkdir -p logs && .venv/bin/leadgen run -p playbooks/my-agency.yaml >> logs/leadgen.log 2>&1
```

On Windows, use Task Scheduler: program `C:\path\to\Lead-Gen-\.venv\Scripts\leadgen.exe`,
arguments `run -p playbooks\my-agency.yaml`, "Start in" `C:\path\to\Lead-Gen-`.

The database remembers what was already handed over, so each daily run's
`instantly_upload.csv` only holds new people: import it each day, or switch on the
`instantly` / `smartlead` exporter to push automatically. `leadgen run` exits with code 1
only when every source failed and nothing was found, which is what you want to alert on.
It is also worth a daily `leadgen followups -p ...` and `leadgen stats -p ...`.

**GitHub Actions** works too: a scheduled workflow (`on: schedule: - cron: "0 7 * * 1-5"`)
that runs `pip install -e .` and `leadgen run ...`, with your keys stored as repository
secrets and passed as `env:`. The database must survive between runs (for example with
`actions/cache` or by uploading it as an artifact; `*.db` files are in `.gitignore`).
Without it the memory is lost and people could be handed over twice.

---

## Handling replies

Replies are sorted into categories and acted on:

| Category | Example | What happens |
|---|---|---|
| `positive` | "Happy to talk, send me some times" | lead -> **positive**; alert with a draft answer + your booking link |
| `question` | "What do your fees look like?" | lead -> replied; alert with a draft answer to fill in |
| `referral` | "Talk to Hannah instead: hannah@..." | lead -> replied; a **new lead** for that person (if an address was given and they're not suppressed or already a lead); alert with a draft intro |
| `timing` | "Not now, try me next quarter" | lead -> replied; follow-up on the date they gave, else in `replies.timing_default_days` (30) |
| `ooo` | an out-of-office auto-reply | no stage change; follow-up the day after they're back, else in `replies.ooo_default_days` (7) |
| `negative` | "Not interested" | lead -> lost; address suppressed; pending follow-ups cancelled |
| `unsubscribe` | "Please remove me" | lead -> lost; address suppressed; pending follow-ups cancelled |
| `bounce` | a mailer-daemon notice | the bounced address's lead -> lost; address suppressed and marked invalid |
| `other` | "Thanks, received" | lead -> replied (automatic notices like "delivery delayed" change nothing) |

Each person has at most one open follow-up: a newer answer replaces the old one (an
out-of-office never pushes back a later "try next quarter"). leadgen doesn't send
follow-ups: `leadgen followups` lists the due ones, you write back, and
`leadgen followups --done <id>` ticks one off. Which categories alert you is set by
`notify.on` (default: positive, referral, question). Remember the company rule from
[Safety](#safety-who-never-gets-emailed): once someone at a company has answered (any
category except `ooo` and automatic notices), that playbook hands over nobody else there.
After a bounce only the dead address is blocked; a colleague can be tried after the
company cooldown.

`replies.classifier`: `rules` (free keyword rules), `ai` (the AI model set in `writer.provider`)
or `auto` (default: the AI when a provider and key are set, otherwise the rules;
bounces, clear out-of-office replies and unsubscribes are always decided by the rules).
The AI is used even when `writer.type` is `template`, as long as `writer.provider` is set.

### Option 1: CSV import

Export replies from your sending tool and run:

```bash
leadgen replies -p playbooks/my-agency.yaml --file replies.csv
```

Columns are matched by name: the sender (`from`, `from_email`, `email` or `sender`;
`Jane Doe <jane@acme.com>` is fine), `subject`, the text (`body`, `text`, `message`,
`reply` or `content`), `received_at` (or `date` / `timestamp`) and optionally `lead_id`.
It prints a table and writes `output/<playbook>/replies_classified.csv` (`--out DIR` for
another folder) with the category, summary, follow-up date, referral, action taken and
suggested reply for each one. Importing the same file twice is safe: replies already
handled are skipped.

### Option 2: webhook server (real time)

Make a random token and put it in `.env`, then start the server:

```bash
python3 -c "import secrets; print(secrets.token_hex(16))"   # prints a random token
# add it to .env:   LEADGEN_WEBHOOK_TOKEN=<the token>
leadgen serve -p playbooks/my-agency.yaml --port 8787
```

(`--token <the token>` works instead of `.env`.) The server prints its addresses and
runs until you press Ctrl+C.

| Endpoint | |
|---|---|
| `GET /health` | `{"ok": true, "playbook": "<name>"}` for uptime checks (no token needed) |
| `POST /webhook/instantly` | Instantly events |
| `POST /webhook/smartlead` | Smartlead events |
| `POST /webhook`, `/webhook/reply` | generic `{"from_email", "subject", "body", "received_at"}` |

All four POST paths understand all three formats; the separate names just make the set-up
easier to read.

The server listens on `127.0.0.1` (this computer) only. To receive webhooks from
Instantly / Smartlead, run it on a machine that stays on, put it behind HTTPS (a reverse
proxy, or a tunnel such as Cloudflare Tunnel or ngrok) and **always set a token**. Most
tools can only set a URL, so add the token as a query parameter:

- **Instantly:** in the webhook settings, add
  `https://your-host/webhook/instantly?token=YOUR_TOKEN` for the *reply received* event.
- **Smartlead:** in the campaign (or account) webhook settings, add
  `https://your-host/webhook/smartlead?token=YOUR_TOKEN` for *Email Reply*.

Bounce and unsubscribe events from both tools are understood too. Menu names change from
time to time; look for "Webhooks" in each tool's settings.

Other events (sent, opened, clicked) are acknowledged and ignored. Requests without the
right token (in `?token=` or an `X-Leadgen-Token` header) get `401`, bad JSON `400`,
bodies over 1 MB `413`. Retried deliveries are recognised and not processed twice.

### Manual updates

```bash
leadgen mark -p playbooks/my-agency.yaml --email jane@acme.com --stage booked   # booked, won, lost, ...
leadgen suppress add competitor.com --reason competitor    # a domain or an email
leadgen suppress add --file do-not-contact.csv             # many at once (TXT: one per line, or a CSV)
leadgen suppress list
leadgen suppress remove competitor.com
```

`mark` only moves a lead forward (`--force` moves it back; `lost` is always allowed).
`--lead ID` works instead of `--email` (the `lead_id` column of `opportunities.csv`).
Like every command, these use the playbook's database with `-p` and `data/leadgen.db`
without it. A suppressed domain blocks everyone at that domain.

---

## Commands

Global options work on every command, before or after it: `-p/--playbook PATH`,
`--db PATH` (use this database instead of the playbook's `storage.path`), `-v` / `-vv`
(more logging + tracebacks), `--env-file PATH` (default `.env`).

| Command | What it does |
|---|---|
| `leadgen init NAME [--template T] [--dir playbooks]` | New playbook from a template (never overwrites). |
| `leadgen validate -p PB [--dry-run]` | Checklist of adapters, config and API keys. Exit 1 on problems. |
| `leadgen run -p PB [--out DIR] [--limit N] [--dry-run]` | One full run into `output/<playbook>/<run id>/` (`--out` picks another folder). Exit 1 only if every source failed. |
| `leadgen demo -p PB [--run ID\|latest] [--top 5] [--prospect NAME] [--no-mask] [--out DIR]` | "Live opportunities" report (Markdown + HTML) in the run's folder. `--top 0` = all. |
| `leadgen leads [-p PB] [--run ID\|latest\|all] [--tier hot\|normal\|skip] [--limit 20]` | Leads as a table (`--limit 0` = all). |
| `leadgen replies -p PB --file CSV [--out DIR]` | Sort + act on replies; writes `replies_classified.csv` (default folder `output/<playbook>/`). |
| `leadgen serve -p PB [--host 127.0.0.1] [--port 8787] [--token T]` | Reply webhook server. |
| `leadgen stats [-p PB] [--since YYYY-MM-DD] [--runs 10]` | Funnel, reply categories, tiers and recent runs. |
| `leadgen mark [-p PB] (--email E \| --lead ID) --stage STAGE [--note N] [--force]` | Move a lead by hand (forward only unless `--force`). |
| `leadgen suppress add\|remove\|list [VALUE] [--kind email\|domain] [--reason R] [--file F]` | Do-not-contact list. |
| `leadgen followups [-p PB] [--days N] [--done ID]` | Follow-ups due today (or in the next N days); `--done` ticks one off. |
| `leadgen adapters` | Every adapter type, whether it's offline, and its key. |

Stages, in order: sourced, qualified, enriched, verified, ready, exported (handed over),
replied, positive, booked, won, plus lost.

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

Put the file in the repo folder, set `LEADGEN_PLUGINS=my_plugins` (in `.env` or the
environment; several modules are separated by commas) and use
`{type: tradeshow, url: ..., show: ...}` in a playbook. `leadgen adapters` and `leadgen
validate` will list it. Rules every adapter follows:

- All HTTP goes through `self.http`, which retries 429/5xx and masks keys in logs. Never import `requests` in an adapter.
- Read keys with `self.secret()` at first use, never in `__init__`.
- Set `offline = True` only if the adapter never touches the network (in `--dry-run` only offline adapters run).
- An exporter that hands leads to a sending tool sets `scope = "outbound"`: it then only gets leads that may be emailed, and the pipeline records them as handed over.
- Parse responses defensively (`.get(...)`).

Tests use `tests.fakes.FakeHttp` with canned responses (see `tests/test_sources_apis.py`).
Run the suite with `pip install -e ".[dev]" && pytest -q`.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `leadgen: command not found` | Activate the venv (`source .venv/bin/activate`, Windows `.venv\Scripts\activate`) or reinstall with `pip install -e .`. |
| `pip` says "externally-managed-environment" | Use a venv, as in the Quickstart. |
| `error: playbook not found` | Run from the repo folder, or pass the full path to `-p`. |
| `[FAIL] ... missing credential: set $X` | Add `X=...` to `.env` (in the folder you run from) or export it. Check with `leadgen validate`. |
| `sourced companies ... 0` in a dry run (sometimes with `warning: the playbook has no sources configured`) | Only csv / json sources run in `--dry-run`. Switch on a csv source to rehearse, or run for real. |
| Everything ends up in `rejected.csv` | Read the reasons. Usually `signals.match_keywords` is too narrow, `max_age_days` too short, or the ICP size/industry too strict. |
| Leads have no email | Add finders (`apollo` -> `hunter` -> `pattern`) and a verifier. `pattern` guesses need a real verifier to become "valid". |
| Note `guessed email only risky (needs valid)` / `... unknown ...` | The address was guessed and couldn't be confirmed. That's the guessed-email rule; use a real verifier or a finder that supplies emails. |
| No emails written | Only `hot`/`normal` leads (`writer.tiers`) with an accepted email (`enrichment.accept_statuses`, `accept_guessed_statuses` for guesses) that can still be handed over get sequences. The `notes` column says why. |
| Notes `already handed over`, `contacted in the last 90 days`, `company contacted 3d ago (cooldown 30d)`, `company already engaged (replied / lost)` | The safety rules working. For testing only, a different `--db` starts from an empty memory (it also forgets who was already emailed, so never do that for real sending). |
| The AI writer isn't used | `leadgen validate` shows why (missing key, provider). A dry run always uses the free template writer. After `writer.max_llm_failures` AI failures in a row the rest of the run uses templates; the lead notes say `ai fallback: <reason>`. |
| `leadgen followups` / `mark` / `suppress` / `leads` can't find the demo's data | Add `-p playbooks/demo-offline.yaml`: without `-p` they use `data/leadgen.db`. |
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
  context.py        run context + the Adapter base class (keys, dry run)
  models.py         Company, Signal, Contact, Lead, Message, Reply, ...
  store.py          SQLite memory: runs, signal history, leads + stages, suppression, replies, follow-ups
  signals.py        which signals count, freshness, reposts
  filters.py        ICP filter (size, industry, location, exclusions)
  contacts.py       buyer-title ranking + the finder waterfall
  scoring.py        0-100 score + tiers
  replies.py        reply cleaning, classification and actions
  report.py         demo report, funnel + run stats
  registry.py       adapter type -> class
  http.py           shared HTTP client (retries, key masking)
  utils.py          small helpers (dates, domains, emails, text)
  sources/          csv/json (+ column mapping), theirstack, adzuna, apollo, apify, greenhouse/lever/ashby
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
