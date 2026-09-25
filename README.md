# leadgen

**A lead-delivery engine for recruitment and staffing agencies.** Every week it
finds companies that have just posted a job for the roles an agency fills, names
the person who owns the hire, labels the email honestly, scores every lead and
writes a client-ready **Hiring Signal Report**: a CSV, an Excel workbook and a
one-page HTML summary.

You sell the report. The agency does its own outreach. leadgen never emails,
calls or messages anyone for you or your clients.

It remembers what each client already received, so a client **never gets the
same company, job or contact twice**. It checks what it can about every email and
says so in the file, so a guessed address is **never labelled "verified"**. And
the default setup costs nothing to run.

leadgen runs in two modes:

| Mode | What it does | Use it for |
|---|---|---|
| **`delivery`** (the default) | find -> filter -> find the decision-maker -> check the email -> score -> **lead files**. Nothing is written or sent to anyone. | The product: `leadgen deliver --client <name>` every week. |
| `outbound` (optional) | Also writes cold-email sequences, hands them to a sending tool (Instantly / Smartlead), sorts replies and tracks follow-ups. | Finding clients for **your own** business (`playbooks/my-agency.yaml`). Kept working, off by default. See [Outbound mode](#outbound-mode-optional). |

**Contents:** [Quick start](#quick-start-delivery-mode-offline-no-api-keys) ·
[Your first real client](#your-first-real-client) ·
[The client file](#the-client-file) · [How a delivery works](#how-a-delivery-works) ·
[Freshness](#freshness) · [The ledger](#the-ledger-never-the-same-thing-twice) ·
[Email statuses](#email-statuses) · [The QA summary](#the-qa-summary) ·
[Opening lines](#suggested-opening-lines) · [Cost control](#cost-control) ·
[leadgen doctor](#live-readiness-leadgen-doctor) ·
[Sources and compliance](#sources-compliance-and-do-not-lists) ·
[Weekly automation](#weekly-automation) · [Playbooks](#playbooks-and-settings) ·
[Scoring](#how-scoring-works) · [Outbound mode](#outbound-mode-optional) ·
[Commands](#commands) · [Extending](#extending-your-own-adapter) ·
[Troubleshooting](#troubleshooting)

---

## What the client gets

One row per company, best leads first (hot, then by score). The columns, in order:

| Column | What it holds |
|---|---|
| Company, Website, Company size, Industry, Location | Who is hiring. |
| Signal type | Why the company is on the list now. `Hiring` = a live job posting. |
| Job title(s) | The open roles that match what the client recruits for (up to 5, then "(+N more)"). |
| Job link, Date posted, Posted | The ad, its date, and "posted 2 days ago" as of the delivery date. |
| Urgency, Score | `hot` / `normal`, and 0-100 (see [scoring](#how-scoring-works)). |
| Decision-maker, Decision-maker title, LinkedIn URL | The person most likely to own the hire, picked from the client's buyer titles. |
| Email, Email status | The address and how far it can be trusted: `verified`, `risky`, `guessed-unverified` or `not found` (see [Email statuses](#email-statuses)). |
| Source | Where the lead came from, e.g. `adzuna` or `demo-jobs; contact via csv`. |
| Suggested opening line | Optional (off by default): one factual line the recruiter can adapt. |

The same rows come in three files, named `<client>-hiring-signals-<YYYY-MM-DD>`:

| File | What it is |
|---|---|
| `.csv` | Opens in Excel / Google Sheets and imports into any CRM. UTF-8 with a BOM, so Excel shows accents correctly. |
| `.xlsx` | A **Leads** sheet (coloured header, filters, clickable links, real dates) and an **About** sheet: who it is for, the period, counts by signal and by email status, a legend of the email labels and a guide to the columns. |
| `.html` | A branded one-page summary: headline numbers (leads, hot leads, verified emails, companies), counts by signal and a table of the top 10 leads. Open it in a browser, or print it to PDF. |

Cells that start with `=`, `+`, `-` or `@` get a leading `'`, so a spreadsheet never
runs them as formulas.

---

## Quick start: delivery mode, offline, no API keys

You need Python 3.9 or newer and git.

```bash
git clone <this repo's URL> Lead-Gen-
cd Lead-Gen-
python3 -m venv .venv            # a private Python just for this project
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .                 # installs the `leadgen` command

leadgen deliver --client demo-client
```

Run every `leadgen` command from this folder with the venv switched on (your prompt
starts with `(.venv)`). Client files, playbooks, sample data, `.env`, `data/` and
`deliveries/` are all found relative to it.

`clients/demo-client.yaml` is a made-up client, *Northstar Finance Recruiting*. It
places accountants, controllers, financial analysts and payroll staff in Texas,
Illinois, New York, Georgia and the UK, and wants 10 leads a week. It runs on
made-up sample data (`playbooks/demo-delivery.yaml`), 100% offline. You'll see:

```
Delivering the Hiring Signal Report for Northstar Finance Recruiting (demo-client) ...

Files for the client in deliveries/demo-client/2026-09-25:
  demo-client-hiring-signals-2026-09-25.csv   spreadsheet - opens in Excel / Google Sheets, imports into any CRM
  demo-client-hiring-signals-2026-09-25.xlsx  Excel workbook - 'Leads' sheet + 'About' sheet (counts, email-status legend)
  demo-client-hiring-signals-2026-09-25.html  one-page summary - open in a browser, or print to PDF

Internal files - for you, not the client - in deliveries/demo-client/2026-09-25/_internal:
  qa.txt                   this QA summary (qa.json: the same as data)
  not_delivered.csv        every company / lead left out, and why
  20260925-030025-021b60/  the pipeline run: rejected.csv, summary.json, opportunities.csv

Delivery QA - Northstar Finance Recruiting (demo-client) - 2026-09-25
  companies found ............ 25
  with a live signal ......... 21
  match the client's criteria  14
  delivered .................. 10 (target 10), 4 hot
  filtered out ............... 13
    top reasons:
      4  location not in ICP [...]
      4  no signal matching [...] in last 7 days
      1  excluded domain
      1  excluded keyword 'staffing' in name
      1  no live job posting
  duplicates removed ......... 0
  on a do-not-list ........... 1
  held back (over the limit) . 2 - not recorded, so they can go in a later delivery
  verified email rate ........ 50% (5 of 10)
  email status ............... verified 5, risky 3, guessed-unverified 1, not found 1
  opening lines .............. 0 AI, 10 template
  API usage: paid lookups 0 (no cap)
    no API calls (free / offline run)

Next: send the files in deliveries/demo-client/2026-09-25 to Jamie Rivera <jamie@northstar-demo.com> at Northstar Finance Recruiting (not the _internal folder - that one is yours).
```

The sample dates are written as "2 days ago", so the demo never goes out of date.

### Look at the files

Open `deliveries/demo-client/<today>/`. Five of the ten rows in the CSV, covering all
four email labels (some columns left out here):

| Company | Job title(s) | Posted | Urgency | Score | Decision-maker | Email status |
|---|---|---|---|---|---|---|
| Hudson Yards Media | FP&A Manager; Senior Financial Analyst | posted 2 days ago | hot | 78 | Nathan Cole, Chief Financial Officer | verified |
| Lonestar Freight Co | Senior Accountant; Accounts Payable Specialist | posted 1 day ago | hot | 73 | Dana Whitfield, CFO | verified |
| Riverbend Health Partners | Payroll Manager; Revenue Cycle Accountant | posted 2 days ago | normal | 60 | Carlos Mendez, Controller | risky |
| Magnolia Home Goods | Accounting Manager | posted 1 day ago | normal | 57 | Grace Holloway, Head of Finance | guessed-unverified |
| Clydeside Engineering Ltd | Assistant Accountant | posted 2 days ago | normal | 46 | Hamish MacLeod, Financial Controller | not found |

The demo client has opening lines switched on, so each row also has one, e.g.
*"Saw Lonestar Freight Co is hiring for 2 roles, including a Senior Accountant in
Houston, TX (posted 1 day ago)."*

`_internal/` is **yours, not the client's**: `qa.txt` / `qa.json` (the summary above),
`not_delivered.csv` (every company left out and why, e.g. `too small (12 employees, min 20)`,
`on this client's do-not-list (company name)`, `over this client's weekly limit of 10 leads`)
and the pipeline run folder (`rejected.csv`, `summary.json`, `opportunities.csv` with
every lead's score reasons).

### Run it again: nothing is delivered twice

Run the same command again, as if it were next week:

```
Delivery QA - Northstar Finance Recruiting (demo-client) - 2026-09-25
  ...
  delivered .................. 2 (target 10), 0 hot
  filtered out ............... 23
    top reasons:
      10  already delivered to this client
  ...
  duplicates removed ......... 10 (already delivered: 10 companies)
  ...
  note: deliveries/demo-client/2026-09-25 already holds a delivery for 2026-09-25 - these files went to deliveries/demo-client/2026-09-25-2 so nothing was overwritten
  WARNING: Low volume: 2 of 10 leads delivered (below this client's weekly target). Most matches were already delivered to this client in earlier weeks. To find more, widen the roles / locations, raise freshness_days or add sources.
```

Only the two leads held back last time come through. A third run finds nothing new,
prints `Nothing to send this time`, and exits with code 1. A real delivery never
overwrites an earlier one on the same day: the files go to `<folder>-2`, `-3`, ...

Try a **preview** too: `leadgen deliver --client demo-client --dry-run` writes the same
files named `...-PREVIEW.csv` / `.xlsx` / `.html` and records nothing, so the real
delivery afterwards is unaffected. The demo keeps its memory in
`data/demo-delivery.db`. Delete that file and `deliveries/demo-client/` to start over.

---

## Your first real client

The default live setup (`playbooks/recruitment-delivery.yaml`) is **free**: job postings
from Adzuna (free API key), guessed emails, and the free offline email check.

**1. Keys.** Get a free Adzuna key at developer.adzuna.com, then:

```bash
cp .env.example .env             # Windows: copy .env.example .env
# edit .env: ADZUNA_APP_ID=...  ADZUNA_APP_KEY=...  SENDER_NAME / SENDER_EMAIL / SENDER_WEBSITE
```

`.env` is read from the folder you run `leadgen` in. Variables already set in your
shell win over it. `.env` is in `.gitignore`, so never commit it.

**2. A client file.**

```
$ leadgen clients new acme
Created clients/acme.yaml from the client template.

Next steps:
  1. Edit clients/acme.yaml: display_name, contact, roles, locations, leads_per_week, exclusions
     (every setting is explained in the file).
  2. leadgen doctor --client acme             # checks the API keys it uses (free calls)
  3. leadgen deliver --client acme --dry-run   # preview files: nothing recorded, no charges
  4. leadgen deliver --client acme             # the real weekly delivery
```

**3. Edit `clients/acme.yaml`.** Every line is commented. For a Texas finance
recruiter, for example:

```yaml
display_name: Acme Staffing Ltd
contact:
  name: Sam Lee
  email: sam@acme-staffing.example
roles: [accountant, controller, financial analyst, payroll]
exclude_roles: [intern, trainee]
buyer_titles: [CFO, VP Finance, Controller, Head of Talent]
locations: [Texas]
exclusions:
  companies: [Their Existing Client Inc]
  keywords: [staffing, recruiting]
leads_per_week: 25
```

A typo is caught before anything runs, for example
`unknown setting 'leads_per_wek' - did you mean 'leads_per_week'?` (exit code 2).

**4. Check the keys** with one free call each. This never makes a paid lookup. With the
Adzuna keys in `.env` it looks like this (your job count will differ):

```
$ leadgen doctor --client acme
Checking the API keys of client 'acme' (Acme Staffing Ltd; base playbook playbooks/recruitment-delivery.yaml), delivery mode ...
  (one free account / credits call per key - never a paid lookup)

status   adapter           detail                                                     quota
-------  ----------------  ---------------------------------------------------------  ------------------------------------------
ok       source adzuna     keys accepted (1-result test search in US, 6 jobs listed)  free API, rate-limited (no quota endpoint)
skipped  finder pattern    no key needed                                              -
skipped  verifier basic    no key needed                                              -
skipped  exporter csv      no key needed                                              -
skipped  notifier console  no key needed                                              -

1 ok, 0 failed, 0 missing key, 4 skipped
Every key that was checked works. Next: leadgen deliver --client acme --dry-run
```

Without the keys that row says
`MISSING KEY  source adzuna  missing key - set $ADZUNA_APP_ID and $ADZUNA_APP_KEY` and the
command exits with 1.

**5. Preview** with `leadgen deliver --client acme --dry-run`. A dry run makes no network
calls at all, and every source in the default playbook is an API. So for this client the
preview is empty. It proves the client file and playbook load, then says:

```
Note: nothing was found because every source uses the network and --dry-run skips them. Add a csv/json source to rehearse offline, or run without --dry-run.
```

To try a real run without recording anything in your real ledger, point it at a scratch
database and folder:

```bash
leadgen deliver --client acme --db data/rehearsal.db --out rehearsal/{client}/{date}
```

**6. Deliver.**

```
$ leadgen deliver --client acme
...
Delivery QA - Acme Staffing Ltd (acme) - 2026-09-25
  companies found ............ 4
  with a live signal ......... 3
  match the client's criteria  2
  delivered .................. 2 (target 25), 0 hot
  ...
  verified email rate ........ 0% (0 of 2)
  email status ............... verified 0, risky 0, guessed-unverified 0, not found 2
  API usage: paid lookups 0 (no cap)
    source adzuna (free): 3 calls

Next: send the files in deliveries/acme/2026-09-25 to Sam Lee <sam@acme-staffing.example> at Acme Staffing Ltd (not the _internal folder - that one is yours).
```

(Example output from a small test search. Your numbers depend on the day's job market.)

> **Know what the free setup gives you.** Adzuna returns the company, the job, its link,
> its date and a location. It does not name people. With only free tools switched on, the
> decision-maker and email columns stay empty (`not found`). To name decision-makers, do
> one of these:
> - switch on Hunter (small free plan, then paid) or Apollo (paid) in
>   `playbooks/recruitment-delivery.yaml`, with a [budget](#cost-control);
> - add a `csv` finder with a contact list you are allowed to use (see `playbooks/demo-delivery.yaml`);
> - switch on TheirStack (paid), which often includes the hiring team.
>
> The free `pattern` finder then guesses emails for people who have a name but no
> address. Those emails are always labelled `guessed-unverified`.

**7. Send the files.** Send everything in the delivery folder **except `_internal/`**:
attach it to an email, share a folder, or set `delivery.google_sheet` in the client
file to push the rows to a Google Sheet you share with the client. leadgen never
sends the report for you.

`leadgen clients` shows every client and their history:

```
client       display name                  leads/week  deliveries  last delivery  total delivered
-----------  ----------------------------  ----------  ----------  -------------  ---------------
acme         Acme Staffing Ltd                     25           1  2026-09-25                   2
demo-client  Northstar Finance Recruiting          10           2  2026-09-25                  12
```

---

## The client file

`clients/<name>.yaml`, one per client. The file name is the client's short name
(lower-case letters, digits, `-`, `_`). Every key is optional; the defaults are in
brackets. `clients/_template.yaml` explains each one.

| Key | Default | What it does |
|---|---|---|
| `display_name` | the file name | Shown on the report. |
| `playbook` | `playbooks/recruitment-delivery.yaml` | The base playbook: sources, how people and emails are found and checked, scoring. Found from the folder you run in, then next to the client file. |
| `contact` | `{}` | `{name, email}` of your contact at the client. For your records and the "Next: send the files to ..." line. |
| `roles` | `[]` (= the playbook's) | Job titles the client fills. A job counts only when its **title** names one. Replaces `signals.match_keywords`. |
| `exclude_roles` | `[]` | Jobs whose title has one of these are ignored (added to the playbook's list). |
| `buyer_titles` | `[]` (= the playbook's) | Decision-maker titles, **best first**. Replaces `buyers.titles`. |
| `locations` / `exclude_locations` | `[]` = anywhere | Where the companies must (not) be, e.g. `[Texas, Oklahoma]` or `[United Kingdom]`. |
| `company_size` | `{min: null, max: null}` | Head-count range; `null` = no limit on that side. |
| `industries` | `[]` = any | Only these industries. |
| `exclusions.companies` | `[]` | Company names this client must never receive (e.g. their existing clients). |
| `exclusions.domains` | `[]` | Website domains to leave out (added to `icp.exclude_domains`). |
| `exclusions.keywords` | `[]` | Leave out companies mentioning these, e.g. `[staffing, recruiting]` (competitors). |
| `leads_per_week` | `25` | Target volume. The file holds at most this many rows; extra leads are held back for later. |
| `freshness_days` | `7` | Only jobs posted in the last N days. |
| `allow_undated` | `false` | Keep jobs without a posting date? |
| `drop_reposts` | `true` | Leave out re-posted ads (see [Freshness](#freshness)). |
| `redelivery_days` | `null` | `null` = never deliver the same item twice; `N` = allowed again after N days. |
| `dedupe` | `[company, job, contact]` | What must never repeat. Remove `company` to allow **new** jobs at companies already delivered. |
| `tiers` | `[hot, normal]` | Which urgency tiers may be delivered (`skip` = weak leads). |
| `emails.include_unverified` | `true` | `false` = only `verified` emails; the others show `not found`. |
| `opening_line.enabled` / `.ai` / `.max_cost_usd` | `false` / `false` / `0.50` | The "Suggested opening line" column; see [Opening lines](#suggested-opening-lines). |
| `budget.max_paid_lookups` | `0` = no cap here | Most paid API requests per delivery; `--budget N` overrides it. |
| `delivery.formats` | `[csv, xlsx, html]` | Any of `csv`, `xlsx`, `html`. |
| `delivery.folder` | `deliveries/{client}/{date}` | Where the files go. `{client}` and `{date}` (YYYY-MM-DD) are filled in. |
| `delivery.google_sheet` | `{spreadsheet_id: "", worksheet: "{date}", service_account_file: ""}` | Optional push to Google Sheets (see [Google Sheets](#google-sheets)). |
| `branding` | `{}` | Change the report's look for this client: `brand_name`, `brand_color`, `sender_name`, `sender_email`, `website`, `logo_url`, `footer`. |
| `overrides` | `{}` | Advanced: any playbook section, merged in last, e.g. `{scoring: {tiers: {hot: 75, normal: 30}}}`. `mode` and `name` cannot be changed. |

A client delivery always runs in delivery mode, names one decision-maker per company,
enriches at most `max(leads_per_week x 2, 10)` companies (or fewer if the playbook says
so), and never builds a hand-over exporter.

Mistakes are reported all at once, each with the key and the fix, e.g.
`freshness_days must be a whole number > 0 (got 'seven')`.

### Google Sheets

Set `delivery.google_sheet.spreadsheet_id` (the long ID in the sheet's address) in the
client file, install the extra package with `pip install -e ".[sheets]"`, and give leadgen
a Google service account. Use either `service_account_file:` in the client file,
`GOOGLE_APPLICATION_CREDENTIALS` (path to the JSON key file), or
`GOOGLE_SERVICE_ACCOUNT_JSON` (the JSON itself) in `.env`. Share the sheet with the
service account's email address. Each delivery replaces the worksheet named by
`worksheet` (default: the date). `leadgen doctor --client <name>` checks the credentials
without contacting Google. A failed push is a QA warning; the files are still complete.
Dry runs never push.

---

## How a delivery works

`leadgen deliver --client acme` does this, in order:

1. **Settings.** It reads `clients/acme.yaml` and its base playbook and merges them
   (the client's targeting on top).
2. **Find.** Each enabled source fetches companies with their job postings. The same
   company from two sources is merged.
3. **Signals.** It keeps jobs whose title names one of the client's `roles` and that are
   fresh enough (see [Freshness](#freshness)). A company left with no matching job is
   dropped.
4. **Client criteria.** Location, size, industry, excluded domains and keywords, and the
   global do-not-list.
5. **Ledger and client do-not-list.** Companies and jobs already delivered to this
   client, and companies on the client's own do-not-list, are removed **before any paid
   lookup**, so no money is spent on leads that can't go out.
6. **Find the person, check the email.** The finders run in order until a decision-maker
   is found. People already delivered to this client, or on a do-not-list, are skipped
   as soon as they are found. The verifier then checks the email of the person picked.
7. **Score** every lead 0-100 and give it a tier: hot, normal or skip.
8. **Select.** Only tiers in `tiers`; only companies with a live job posting; one row per
   company and per person; hot first, then by score; cut to `leads_per_week`.
9. **Files.** CSV / Excel / HTML (and the optional Google Sheets push).
10. **Record** every delivered company, job and person in the ledger (not in a dry run).
11. **QA summary** printed and saved in `_internal/`.

---

## Freshness

The report sells timing, so only fresh jobs count:

- `freshness_days: 7` keeps jobs posted in the last 7 days (as of the delivery date).
  The "Posted" column says `posted today` / `posted 3 days ago`.
- `allow_undated: false` leaves out jobs without a posting date, because nobody can
  promise they are fresh. With `true`, they show `date unknown (first seen N days ago)`.
- `drop_reposts: true` leaves out re-posted ads: an old job put up again to look new.
  leadgen keeps a history of every job it has seen. An ad counts as re-posted when the
  same title at the same company was seen in an earlier run under a different ad that
  has since gone, or when leadgen first saw it at least a week before its new posting
  date. (The demo shows a related case: Lakeshore's "Cost Accountant" has a new ad
  2 days old and an old ad 24 days old. Only the new ad is delivered, because the old
  one is outside the window.)

Several jobs at one company are listed together in "Job title(s)", freshest first.

---

## The ledger: never the same thing twice

Every delivery records, per client, the **company**, every **job** in the row, and every
way to recognise the **person**: email, LinkedIn URL, and name at company. The next
delivery removes all of them (`dedupe`):

- **company** in `dedupe` (default): a company is delivered to a client once, ever.
- remove `company` from `dedupe`: a company already delivered can come back **with a new
  job only**. The jobs already sent are stripped, and if none is left the company is
  dropped with "all its jobs were already delivered". If the person already delivered is
  the best match, the next best person is used instead.
- **job**: the same ad (by its ID, or its link without tracking parameters) never repeats.
- **contact**: the same person never repeats. A person counts as the same when the email,
  the LinkedIn profile, or the name at the same company matches.
- `redelivery_days: 90` allows an item again 90 days after it was delivered.
  `null` (default) = never.

Leads **held back** by `leads_per_week` are not recorded, so they can go out next time.
**Dry runs record nothing.** The ledger lives in the playbook's database
(`storage.path`, default `data/leadgen.db`; the demo uses `data/demo-delivery.db`). Each
client has its own history, so two clients can receive the same company. **Keep this
file safe:** without it leadgen can't know what was already delivered.

---

## Email statuses

Every email in a delivery carries exactly one of four labels:

| Label | Exact meaning |
|---|---|
| `verified` | The mailbox was confirmed deliverable, either by an email-checking service (MillionVerifier, ZeroBounce, NeverBounce, Hunter) or by the data provider or list that supplied it. **And** the address was not built from a name pattern. |
| `risky` | A real address from a provider or list that is not confirmed: the company's mail server accepts every address ("catch-all"), or nobody checked it. Usually works; expect some bounces. |
| `guessed-unverified` | Built from a name pattern (`first.last@company.com`, ...) by the `pattern` finder. **Always** this label, whatever a checker says. A guess is never "verified". |
| `not found` | No usable email in this file: none found, it failed verification (invalid), or the client's email policy withheld it. The LinkedIn URL and website are still there. |

**Why a guess is never "verified".** Many companies' mail servers accept every
address, and an email-checking service can't always tell a real inbox from a
catch-all. A guessed address that "passed" can still belong to nobody. Recruiters
judge a lead list by its bounce rate. So the file promises only what is known.

`emails.include_unverified: false` delivers **only** `verified` emails. Every other
email is removed and the row says `not found`. The rest of the row (company, job,
person, LinkedIn) is still delivered.

The free `basic` checker can't confirm any mailbox. It only catches typos and throw-away
domains. So on the free setup, `verified` only comes from a list or provider that marks
an address as verified (like the demo's contact list). A paid checker turns provider
addresses into `verified`, but guesses still stay `guessed-unverified`.

---

## The QA summary

Printed after every delivery and saved as `_internal/qa.txt` (and `qa.json`):

| Line | Meaning |
|---|---|
| companies found | Companies the sources returned (after merging duplicates). |
| with a live signal | ... with at least one fresh, matching job. |
| match the client's criteria | ... that also pass location / size / industry / exclusions. |
| delivered (target N), M hot | Rows in the file vs `leads_per_week`. |
| filtered out + top reasons | Everything left out, grouped (details are in `_internal/not_delivered.csv`). |
| duplicates removed | Already delivered to this client, plus the same company or person twice in this run. |
| on a do-not-list | Removed by the global or the client's do-not-list. |
| held back (over the limit) | Good leads over `leads_per_week`; not recorded, so they can go out next time. |
| verified email rate | `verified` emails / rows delivered. |
| email status | Count per label. |
| opening lines | AI vs template lines, and their cost. |
| API usage | Paid lookups (and the cap), calls per adapter, credits left, estimated cost. |

**Warnings** appear when there are 0 leads ("Don't send them to the client"), volume is
below target (with a hint when most matches were already delivered), the paid-lookup
budget ran out, a source failed, the AI cost cap was hit, or the Google Sheets push
failed.

Exit codes: `0` delivered; `1` nothing delivered (or every source failed); `2` a
problem with the command or the client file (one `error:` line says what).

---

## Suggested opening lines

Off by default. Switch it on per client:

```yaml
opening_line:
  enabled: true        # adds the "Suggested opening line" column
  ai: false            # false = free template line
  max_cost_usd: 0.50   # AI only: most AI spend per delivery
```

**Template lines** (free, instant) state only facts from the row:
*"Saw Magnolia Home Goods is hiring an Accounting Manager in Atlanta, GA (posted 1 day ago)."*
Missing facts are simply left out.

**AI lines** (`ai: true`) use the AI provider and model from the base playbook's
`writer` section, or from the client file's `overrides`, plus its key in `.env`:

```yaml
overrides:
  writer: {provider: anthropic, model: claude-haiku-4-5}   # key: ANTHROPIC_API_KEY
```

The AI sees only company-level facts (company, role, location, date, job snippet),
never the decision-maker's name or email. It is told not to invent anything. An
answer with a number that isn't in the facts, a link, an email or a placeholder is
thrown away and the template line is used. Before each call, leadgen estimates the
worst-case cost. If that could push the run over `max_cost_usd`, it switches to template
lines for the rest of the run and the QA summary warns. Errors, a missing key and dry
runs also fall back to template lines. It never fails the delivery.

---

## Cost control

**The default path is free:** Adzuna (free key), company job boards (Greenhouse /
Lever / Ashby) and your own CSV files for jobs; the `pattern` finder for email guesses;
the `basic` checker. Paid tools are listed in the playbooks but switched off
(`enabled: false`). `leadgen adapters` marks every paid type as `network (paid)`:

| Paid ("paid lookup" = one request) | Free |
|---|---|
| TheirStack, Apollo (source + finder), Apify, linkedin_jobs, Hunter (finder + verifier), MillionVerifier, ZeroBounce, NeverBounce, AI (Anthropic / OpenAI / compatible) | Adzuna, Greenhouse, Lever, Ashby, csv / json, csv + pattern finders, basic checker, file exporters, console |

**Cap the paid lookups per run.** The first one set wins: `--budget N`, then the
client's `budget.max_paid_lookups`, then the playbook's `usage.max_paid_lookups`.
In a client file `0` means "no cap of its own" (the playbook's cap still applies); in
the playbook, and with `--budget 0`, it means no cap at all.

```bash
leadgen deliver --client acme --budget 200
leadgen run -p playbooks/recruitment-delivery.yaml --budget 50
```

When the cap is reached, paid requests are refused **before** they reach the network.
Paid sources and finders stop, the verifier falls back to the free `basic` checker, free
tools keep working, and the QA summary warns. For no paid calls at all, use `--dry-run`.

**See what it cost.** Every run prints the calls per adapter. Add your plan's price per
request to the playbook to get an estimate:

```yaml
usage:
  cost_per_call: {millionverifier: 0.004, hunter: 0.03, apollo: 0.05}   # USD, from YOUR plan
```

For example, a delivery with a paid email checker and `--budget 3`:

```
  API usage: paid lookups 3/3
    verifier millionverifier (paid): 3 calls, 9497 credits left, ~$0.0120
    estimated cost: ~$0.0120
  WARNING: Paid-lookup budget reached (3 of 3 paid lookups used): some contacts / emails were not looked up with the paid providers. Raise budget.max_paid_lookups in the client file or run with --budget N.
```

AI tokens are priced from a built-in table of Anthropic list prices. For other models,
set `usage.llm_price_per_mtok: {model-name: {input: 1.0, output: 5.0}}` (USD per
million tokens). An unknown model is priced high on purpose, so AI cost caps err on
the safe side. Providers that report credits left (MillionVerifier) show them too.
Other ways to save money: `enrichment.max_companies`, `--limit N` on `leadgen run`, and
the ledger, which drops already-delivered companies before any lookup.

---

## Live readiness: `leadgen doctor`

`leadgen doctor --client acme` (or `-p playbooks/X.yaml`) tests **every key** the
client or playbook uses with **one free call** each: an account, credits or model-list
endpoint, never a paid lookup, and never counted against `--budget`. Each row is:

| Status | Meaning |
|---|---|
| `ok` | The key works. The quota column shows credits / plan when the provider reports them. |
| `failed` | The key was rejected, the service could not be reached, or its answer was unexpected (the detail says which). |
| `MISSING KEY` | The key isn't set; the detail names the variable. |
| `skipped` | No key needed (free / offline adapters), or no free check exists (Slack / webhooks would post a real message). |

It exits with 1 when any row is FAILED or MISSING KEY. `--dry-run` only checks that
keys are **set** and contacts nobody. With `--client`, and a client file that sets a
Google Sheet, the Sheets credentials are checked too (without contacting Google). Keys
never appear in the output.

`leadgen validate -p playbooks/X.yaml` is the offline companion. It checks that every
adapter type exists, that the config is complete, and which keys are set, and shows the
mode.

---

## Sources, compliance and do-not-lists

**Prefer sources that allow this use.** The shipped playbooks use official APIs
(Adzuna, TheirStack, Apollo, Hunter), companies' own public job boards (Greenhouse,
Lever, Ashby) and files you supply. **Scraping LinkedIn or Indeed** (the `linkedin_jobs`
source, and the Apify `linkedin_jobs` / `indeed_jobs` presets) goes against those sites'
terms. Those adapters exist, but they are marked **"use at own risk"** in
`leadgen adapters`, flagged by `leadgen validate`, and **never used by the default
playbooks**.

**Do-not-lists.** Anyone who asks not to be listed, and any company that should never
appear, goes on a list. There are two:

```bash
# global: never delivered to ANY client (and never contacted in outbound mode)
leadgen suppress add jane.doe@example-corp.com --reason "asked not to be listed"
leadgen suppress add https://www.linkedin.com/in/jane-doe-123
leadgen suppress add example-corp.com                      # a whole domain: everyone there
leadgen suppress add "Example Corp" --kind company

# one client's own list (e.g. their existing clients)
leadgen suppress add bigclient.com --client acme --reason "their client"
leadgen suppress list --client acme
leadgen suppress remove bigclient.com --client acme
```

Without `--kind`, a linkedin.com URL counts as LinkedIn, a value with `@` as an email, and
anything else as a domain. Company names always need `--kind company`. `--file list.csv`
adds many at once. Companies and domains on a list are removed before any paid lookup.
People (email, LinkedIn) are skipped as soon as they are found, before their email is
checked. `suppress list --client acme` also shows the client file's own `exclusions`.

The global list lives in a **database file**. Without `-p` or `--db` it is
`data/leadgen.db`, which is what clients on `recruitment-delivery.yaml` use. For the demo,
add `-p playbooks/demo-delivery.yaml` (or `--db data/demo-delivery.db`). A client list
(`--client`) automatically uses that client's database.

leadgen has no country-specific legal rules built in. The report contains names, job
titles and work emails of real people. Check the data-protection and marketing rules
that apply to you, your clients and the people listed (and your data providers' terms)
before you sell it. The do-not-lists are there to help.

---

## Weekly automation

A delivery is one command, so a weekly schedule is one line per client. With cron
(Linux / macOS, `crontab -e`), from the repo folder so `.env` and the relative paths
are found:

```cron
# m  h  dom mon dow  command            (Mondays at 06:45)
45 6 * * 1  cd /path/to/Lead-Gen- && mkdir -p logs && .venv/bin/leadgen deliver --client acme >> logs/deliver-acme.log 2>&1
50 6 * * 1  cd /path/to/Lead-Gen- && .venv/bin/leadgen deliver --client northwind >> logs/deliver-northwind.log 2>&1
```

On Windows, use Task Scheduler: program `C:\path\to\Lead-Gen-\.venv\Scripts\leadgen.exe`,
arguments `deliver --client acme`, "Start in" `C:\path\to\Lead-Gen-`.

Then read the QA summary in the log (or `_internal/qa.txt`) and send the files.
Automating the check is good; the sending stays with you. A non-zero exit code
(1 = nothing delivered, 2 = configuration problem) is what to alert on. A weekly
`leadgen doctor --client acme` catches expired keys before delivery day. If you run
it on GitHub Actions or a server, the database (`data/*.db`) must survive between
runs, or the ledger forgets what was delivered.

---

## Playbooks and settings

A **playbook** (YAML) says where leads come from, how people and emails are found and
checked, and how leads are scored. A **client file** adds one client's targeting on top.

| Playbook | Mode | What it is |
|---|---|---|
| `playbooks/recruitment-delivery.yaml` | delivery | The default base for client files. Free as shipped: Adzuna (`countries: [us]`, finance queries), pattern finder, basic checker. Greenhouse / Lever / Ashby watchlists, a CSV import, TheirStack, Apollo and Hunter are ready but switched off. |
| `playbooks/demo-delivery.yaml` | delivery | The offline demo: sample jobs + a sample contact list. |
| `playbooks/demo-offline.yaml` | outbound | The outbound demo (see [Outbound mode](#outbound-mode-optional)). |
| `playbooks/my-agency.yaml` | outbound | Finding clients for **your own** business. |
| `playbooks/templates/*.yaml` | | `generic` (delivery mode; lists every setting), `recruitment`, `saas-funding`, `local-business`, `agency-outreach` (outbound). |

**A client in another niche** (nurses, drivers, engineers ...): the source search words
decide what is **fetched** (Adzuna `queries`, TheirStack `job_titles`). The client's
`roles` decide what **counts**. Copy `recruitment-delivery.yaml` (e.g. to
`playbooks/delivery-nursing.yaml`), change the search words, and set that client's
`playbook:` line to the copy. `leadgen run -p playbooks/recruitment-delivery.yaml` runs a
playbook on its own, without a client or ledger, and writes a review file
(`opportunities.csv`) to `output/<playbook>/<run id>/`.
`leadgen init NAME --template generic` starts a new playbook from a template.

### The settings you'll change most

Leave a setting out and its default is used (in brackets). `playbooks/templates/generic.yaml`
explains every one.

| Section | Setting | What it does |
|---|---|---|
| | `mode` [delivery] | `delivery` = lead files only; `outbound` = also write + hand over + replies. |
| `delivery` | `brand_name` [Hiring Signal Report], `brand_color`, `sender_name`, `sender_email`, `website`, `logo_url`, `footer` | The report's look and footer (clients can override under `branding`). |
| `icp` | `locations`, `employees: {min, max}`, `industries` | Who fits (client files replace these). Empty = anyone. |
| | `exclude_industries`, `exclude_keywords`, `exclude_domains` | Competitors (other agencies), existing clients. |
| | `require_domain` [false], `unknown_passes` [true] | Keep companies without a website / with missing data. |
| `signals` | `types` [all], `primary` [job_posting] | Which signals count; primary ones drive the score, the rest are bonus points. |
| | `match_keywords`, `exclude_keywords`, `match_description` [true] | Roles that count (client `roles` replace them); `false` = the title itself must name the role. |
| | `max_age_days` [60], `allow_undated` [true], `drop_reposts` [false] | Freshness (client files set these; the delivery playbooks use 7 / false / true). |
| `buyers` | `titles` | Decision-maker titles, **best first**. |
| | `max_contacts_per_company` [1], `allow_generic_emails` [false] | People per company; allow info@ / jobs@ inboxes. |
| `sources` | list of `{type: ..., ...}` | Where companies come from. Each takes `label` and `enabled: false`. |
| `enrichment` | `finders`, `verifier` [basic] | Finders run in order until someone is found; then the email is checked. |
| | `accept_statuses` [valid, risky], `accept_guessed_statuses` [valid] | Which checker results count. In delivery mode they only decide **which** person goes in the file (the delivery playbooks accept `unknown` too); every email is still labelled honestly. |
| | `max_companies` [200] | Credit guard: only the best N companies per run are enriched. |
| `scoring` | `weights`, `tiers` [hot 80, normal 60] | See [How scoring works](#how-scoring-works). |
| `usage` | `max_paid_lookups` [0], `cost_per_call` [{}], `llm_price_per_mtok` [{}] | See [Cost control](#cost-control). |
| `writer` | `provider`, `model` | The AI for opening lines (delivery) or emails (outbound). The other `writer` settings are for outbound emails (`max_llm_failures` also applies to AI opening lines). |
| `outbound` | `exporters` [csv] | Review files for `leadgen run` (`csv`, `json`, `gsheets`). Hand-over exporters are outbound only. |
| `notify` | `channels` [console], `"on"` | Where run summaries / alerts go (console, Slack, webhook). Keep `"on"` in quotes. |
| `storage` | `path` [data/leadgen.db] | The database file: the ledger, do-not-lists, job history, email-check cache. |

Playbooks can read your own variables: `${SENDER_NAME}` or `${SENDER_NAME:-default}`
anywhere in the YAML is replaced from the environment / `.env`.

### Which key does what

Only the adapters switched on in your playbook need keys. `leadgen adapters` lists every
type, whether it's offline / network / paid, its key and any risk note.

| Adapter (`type:`) | Kind | Env var(s) | Needed for |
|---|---|---|---|
| `csv`, `json` | source | none | Your own job lists (columns auto-detected). |
| `adzuna` | source | `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` (free) | **The default live delivery playbook.** `countries:` is required, e.g. `[us]`, `[gb, ca]`. |
| `greenhouse`, `lever`, `ashby` | source | none | A watchlist of companies' public job boards. |
| `theirstack` | source | `THEIRSTACK_API_KEY` (paid) | Job postings with company data and hiring teams. |
| `apollo` | source + finder | `APOLLO_API_KEY` (paid) | Company search; people search + email reveal. |
| `apify` | source | `APIFY_TOKEN` (paid) | Any Apify scraper. The LinkedIn / Indeed presets are use-at-own-risk. |
| `linkedin_jobs` | source | `APIFY_TOKEN` (paid) | **Use at own risk** (LinkedIn's terms forbid scraping). Not in any default playbook. |
| `hunter` | finder + verifier | `HUNTER_API_KEY` (small free plan, then paid) | People + emails at a domain; email checks. |
| `csv`, `pattern` | finder | none | Your own contact list; free email-pattern guesses. |
| `basic` | verifier | none | Free, offline syntax + throw-away-domain check (can't confirm a mailbox). |
| `millionverifier` / `zerobounce` / `neverbounce` | verifier | `MILLIONVERIFIER_API_KEY` / `ZEROBOUNCE_API_KEY` / `NEVERBOUNCE_API_KEY` (paid) | Real email checks: valid / risky (catch-all) / invalid. Cached for 30 days. |
| writer `provider: anthropic` | llm | `ANTHROPIC_API_KEY` (paid) | AI opening lines; outbound: AI emails + reply sorting. `claude-haiku-4-5` is the cheapest. |
| writer `provider: openai` / `openai_compatible` | llm | `OPENAI_API_KEY` / the var in `writer.api_key_env` (paid) | The same with OpenAI or a compatible API (`base_url` + `model`). |
| client `delivery.google_sheet`, `gsheets` exporter | | `GOOGLE_APPLICATION_CREDENTIALS` or `GOOGLE_SERVICE_ACCOUNT_JSON` | Google Sheets (`pip install -e ".[sheets]"`). |
| `slack` / `webhook` | notifier | `SLACK_WEBHOOK_URL` / `LEADGEN_WEBHOOK_URL` | Run summaries and alerts. |
| `instantly`, `smartlead`, `webhook` exporters, `leadgen serve` | outbound only | `INSTANTLY_API_KEY`, `SMARTLEAD_API_KEY`, `LEADGEN_EXPORT_WEBHOOK_URL`, `LEADGEN_WEBHOOK_TOKEN` | See [Outbound mode](#outbound-mode-optional). |

To read a key from a differently named variable, add `api_key_env: OTHER_NAME` to that
adapter's entry. `.env.example` lists every variable.

---

## How scoring works

Every lead gets 0-100 from four parts (`scoring.weights`):

| Part | Earned by |
|---|---|
| **Intent** | The *primary* signal (a matching job): **fresh** (full points in the first `scoring.freshness_days` band, then less), **volume** (several matching open roles), **persistence** (re-posted or still open after `stale_after_days` = hard to fill), **urgency** ("urgent", "asap", "immediate" ...). |
| **Fit** | Location, size and industry each match or aren't set (full), are unknown (half) or don't match (none). |
| **Reachability** | Half for the person (top buyer title 100%, #2-3 80%, lower 60%, other titles 30%), half for the email (valid 100%, risky 50%, unverified 30%). |
| **Extra** | +5 per bonus signal type (funding, expansion, leadership change ...). |

The engine defaults are weights 40 / 30 / 20 / 10 and tiers hot >= 80, normal >= 60.
**The delivery playbooks are tuned for hiring signals:** weights 45 / 15 / 30 / 10,
freshness bands 2 / 4 / 7 / 14 days, and tiers **hot >= 65, normal >= 15**. So any
matching job inside the week is deliverable, even at a company with no size data and
nobody named yet, and `skip` means nothing fresh. The reasons for each score are in
`_internal/<run>/opportunities.csv`, e.g. for the demo's Hudson Yards Media:
`fresh signal (2d); 2 matching signals; location match; size match; industry match;
Chief Financial Officer = buyer #1; email valid; +funding`.

---

## Outbound mode (optional)

Outbound mode is the engine's original job: **finding clients for your own business**.
It writes a short personal cold-email sequence per lead, hands the leads to your sending
tool, sorts the replies and tracks follow-ups. It is kept working but switched off by
default. Nothing in delivery mode uses it.

Turn it on with `mode: outbound` in a playbook. `playbooks/my-agency.yaml` is set up
to find clients for **your own** business. It looks for B2B service firms (recruitment,
marketing, IT services, consultancies) that are hiring sales / business-development
people, and writes to their founder or MD. Its `offer` section still pitches a
lead-generation agency, so rewrite `service`, `value_prop` and `proof` to pitch the
Hiring Signal Report before you use it. Switch it to `mode: delivery` if you only want
the list. `replies`, `serve` and `followups` refuse to run on a delivery-mode playbook,
or without `-p`:

```
$ leadgen followups
error: leadgen followups is an outbound-mode feature, and this playbook runs in delivery mode (the default). Add 'mode: outbound' to the playbook to use it.
No playbook was given (-p), so the default mode (delivery) applies. Name an outbound-mode playbook, e.g. leadgen followups -p playbooks/demo-offline.yaml
```

### Outbound demo (offline, no keys)

```bash
leadgen run -p playbooks/demo-offline.yaml
```

```
Run 20260925-030258-c5e6cc (demo-offline)
  sourced companies ....... 18
  with a live signal ...... 15
  match the ICP ........... 11
  decision-maker found .... 9
  email verified .......... 9
  hot / normal / skip ..... 3 / 4 / 4
  sequences written ....... 7
  handed to outbound ...... 7
  output .................. output/demo-offline/20260925-030258-c5e6cc
    - instantly_csv: 7 output/demo-offline/20260925-030258-c5e6cc/instantly_upload.csv
    - smartlead_csv: 7 output/demo-offline/20260925-030258-c5e6cc/smartlead_upload.csv
    - csv: 11 output/demo-offline/20260925-030258-c5e6cc/opportunities.csv
    - json: 11 output/demo-offline/20260925-030258-c5e6cc/leads.json
```

In `output/demo-offline/<run id>/`: `opportunities.csv` (every lead with score, reasons
and the full sequence), `instantly_upload.csv` / `smartlead_upload.csv` (the people
handed over, in each tool's import format), `leads.json`, `rejected.csv`, `summary.json`.
Writing an upload file counts as handing those people over. Then:

```bash
leadgen leads -p playbooks/demo-offline.yaml            # the leads as a table
leadgen demo  -p playbooks/demo-offline.yaml --prospect "Northbeam Talent"   # one-page report
leadgen replies -p playbooks/demo-offline.yaml --file examples/data/demo_replies.csv
leadgen followups -p playbooks/demo-offline.yaml --days 30   # follow-ups coming up
leadgen stats -p playbooks/demo-offline.yaml            # the funnel
leadgen run   -p playbooks/demo-offline.yaml            # again: nobody is handed over twice
```

`replies` sorts 12 sample replies (`By category: positive 3, referral 1, timing 1,
question 1, negative 1, unsubscribe 1, ooo 1, bounce 1, other 2`), moves leads to
replied / positive / lost, suppresses the unsubscribe, the "not interested" and the
bounce, and schedules follow-ups. The second run shows `sequences written ... 0` and
`handed to outbound ... 0`. The outbound demo uses `data/demo.db`.

`leadgen demo` also works on delivery-mode runs:
`leadgen demo -p playbooks/demo-delivery.yaml --prospect "Acme Staffing"` makes a
"live opportunities" one-pager (emails masked) to show a prospective agency client
what the report looks like.

### Going live with outbound

1. Fill in `.env`: `SENDER_NAME`, `SENDER_TITLE`, `SENDER_COMPANY`, `SENDER_WEBSITE`,
   `BOOKING_LINK` and the keys `my-agency.yaml` uses.
2. `leadgen validate -p playbooks/my-agency.yaml` and `leadgen doctor -p playbooks/my-agency.yaml`.
3. `leadgen run -p playbooks/my-agency.yaml --dry-run` rehearses for free: only csv / json
   sources run, the template writer replaces the AI, and upload files are written as
   `*.dry-run.csv` (never import those).
4. `leadgen run -p playbooks/my-agency.yaml --limit 10 --budget 100` is a small paid test.
5. Review `opportunities.csv`, then import `instantly_upload.csv` into your campaign (or
   switch on the `instantly` / `smartlead` exporter to push straight into it).

In the campaign, make one step per email using the upload file's variables: step 1
subject `{{subject_1}}`, body `{{email_1}}`; steps 2-4 an empty subject (same thread),
bodies `{{email_2}}` ... `{{email_4}}`. Match the timing to `writer.sequence` (default
days 1, 3, 7, 12). Use your sending tool's warmed-up inboxes on separate domains,
never your main domain.

**Writer settings** (`writer`): `type` [template] or `ai` with `provider` / `model`,
`max_words` [90], `sequence`, `tone`, `extra_instructions`, `banned_phrases`,
`max_tokens` [4000], `max_llm_failures` [3], `acronyms`, `llm` (client options),
`fallback_to_template` [true], `max_leads` [500], `tiers` [hot, normal]. AI copy is
checked for length, banned phrases, placeholders, links and shouting. A failed check
gets one retry, then the free template takes over.

### Outbound safety rules (built in)

- **Nobody is handed over twice** by the same playbook. The same email arriving through
  another playbook or company record is blocked for `outbound.dedupe_days` (90).
- **One email per address per run.** The higher-scored lead wins.
- **Company cooldown:** no colleague at a company contacted in the last
  `outbound.company_cooldown_days` (30). Once anyone there replied, unsubscribed, said
  no or was marked lost, that playbook never contacts the company again. A bounce only
  blocks the dead address.
- **The do-not-list** (`leadgen suppress`, shared with delivery mode): "no",
  unsubscribe and bounce replies are added automatically.
- **Guessed emails need proof:** a pattern guess is handed over only when a real checker
  says `valid` (`enrichment.accept_guessed_statuses`, default `[valid]`). The offline
  demo loosens this because nothing can be confirmed offline. Never copy that into a
  live playbook.
- **No AI money on dead ends:** emails are only written for leads that can be handed
  over. After `writer.max_llm_failures` AI failures in a row, templates take over.
- **Dry runs are rehearsals:** no network calls, nobody recorded as handed over.
- **Secrets stay out of logs:** keys, tokens and webhook URLs are masked everywhere.

You are responsible for the cold-email rules wherever you and the people you email
are. `offer.footer` (an opt-out line), the do-not-list and the automatic unsubscribe
handling are there to help.

### Replies

| Category | What happens |
|---|---|
| `positive` | lead -> positive; alert with a draft answer + your booking link |
| `question` | lead -> replied; alert with a draft answer |
| `referral` | lead -> replied; a new lead for the person named (unless suppressed / already a lead) |
| `timing` | lead -> replied; follow-up on the date given, else in `replies.timing_default_days` (30) |
| `ooo` | no stage change; follow-up the day after they're back, else in `replies.ooo_default_days` (7) |
| `negative` / `unsubscribe` | lead -> lost; address suppressed; follow-ups cancelled |
| `bounce` | that address's lead -> lost; suppressed and cached as invalid |
| `other` | lead -> replied (automatic notices change nothing) |

`replies.classifier`: `rules` (free), `ai` (the `writer.provider` model) or `auto`
(default: AI when configured, else rules; bounces, clear out-of-office replies and
unsubscribes are always decided by the rules). Which categories alert you is set by
`notify.on`.

**CSV import:** `leadgen replies -p playbooks/my-agency.yaml --file replies.csv`. Columns
are matched by name: sender (`from`, `from_email`, `email`), `subject`, text (`body`,
`text`, `message`), `received_at`, optional `lead_id`. Importing the same file twice is
safe. The results go to `output/<playbook>/replies_classified.csv`.

**Webhook server (real time):**

```bash
python3 -c "import secrets; print(secrets.token_hex(16))"   # a random token -> LEADGEN_WEBHOOK_TOKEN in .env
leadgen serve -p playbooks/my-agency.yaml --port 8787
```

It listens on `127.0.0.1` only: `GET /health`, and `POST /webhook`, `/webhook/reply`,
`/webhook/instantly`, `/webhook/smartlead` (each understands Instantly, Smartlead and
generic `{from_email, subject, body, received_at}` payloads). Put it behind HTTPS (a
reverse proxy or a tunnel) and give the tools `https://your-host/webhook/instantly?token=YOUR_TOKEN`.
**Always set a token** (`LEADGEN_WEBHOOK_TOKEN` or `--token`). With a token, requests
without it get 401. Without one, the server accepts every request and warns
`auth: none` when it starts. Retried deliveries are not processed twice.

**By hand:**

```bash
leadgen followups -p playbooks/my-agency.yaml               # follow-ups due today
leadgen followups -p playbooks/my-agency.yaml --done 2      # tick one off
leadgen mark -p playbooks/my-agency.yaml --email jane@acme.com --stage booked   # booked, won, lost, ...
leadgen stats -p playbooks/my-agency.yaml                   # funnel + reply categories
```

`mark` only moves a lead forward (`--force` moves it back; `lost` is always allowed).
Stages: sourced, qualified, enriched, verified, ready, exported (handed over), replied,
positive, booked, won, plus lost. Rough targets for good outbound: bounce rate under 2%,
reply rate 3-8%, positive replies 1-3% of emails sent. For a daily outbound run, add a
cron line with `leadgen run -p playbooks/my-agency.yaml`. It exits 1 only when every
source failed.

---

## Commands

Global options work before or after any command: `-p/--playbook PATH`, `--db PATH` (this
database instead of the playbook's `storage.path`), `-v` / `-vv` (more logging +
tracebacks), `--env-file PATH` (default `.env`). `python -m leadgen ...` works the same
as `leadgen ...`.

**Delivery (the product)**

| Command | What it does |
|---|---|
| `leadgen deliver --client NAME [--dry-run] [--budget N] [--out DIR] [--clients-dir DIR]` | One client's Hiring Signal Report (`clients/NAME.yaml`). `--dry-run` = PREVIEW files, nothing recorded, no network. `--out` accepts `{client}` / `{date}`. `--db` picks the ledger database (default: the client playbook's `storage.path`). Exit 1 when nothing was delivered. |
| `leadgen clients [list]` / `leadgen clients new NAME` | Every client with leads/week, deliveries, last delivery and total delivered / a new client file from the template (never overwrites). |
| `leadgen doctor (-p PB \| --client NAME) [--dry-run]` | Tests every key with one free call each (never a paid lookup). Exit 1 on FAILED / MISSING KEY. |
| `leadgen suppress add\|remove\|list [VALUE] [--kind email\|domain\|company\|linkedin] [--client NAME] [--reason R] [--file F]` | The global do-not-list, or with `--client` that client's own list. |
| `leadgen run -p PB [--dry-run] [--budget N] [--limit N] [--out DIR]` | One pipeline run without a client (review files in `output/<playbook>/<run id>/`). Exit 1 only if every source failed. |
| `leadgen validate -p PB [--dry-run]` | Offline checklist: mode, adapters, config, which keys are set. Exit 1 on problems. |
| `leadgen adapters` | Every adapter type: offline / network / paid, its key, notes (use-at-own-risk, outbound only). |
| `leadgen init NAME [--template T] [--dir playbooks]` | A new playbook from a template (never overwrites). |

**Reports**

| Command | What it does |
|---|---|
| `leadgen demo -p PB [--run ID\|latest] [--top 5] [--prospect NAME] [--no-mask] [--out DIR]` | "Live opportunities" one-pager (Markdown + HTML) for a run. |
| `leadgen leads [-p PB] [--run ID\|latest\|all] [--tier hot\|normal\|skip] [--limit 20]` | The leads of a run as a table. |
| `leadgen stats [-p PB] [--since YYYY-MM-DD] [--runs 10]` | Funnel, tiers, replies and recent runs. |

**Outbound mode only**

| Command | What it does |
|---|---|
| `leadgen replies -p PB --file CSV [--out DIR]` | Sort and act on replies; writes `replies_classified.csv`. |
| `leadgen serve -p PB [--host 127.0.0.1] [--port 8787] [--token T]` | Reply webhook server. |
| `leadgen followups -p PB [--days N] [--done ID]` | Follow-ups due (in the next N days); `--done` ticks one off. |
| `leadgen mark [-p PB] (--email E \| --lead ID) --stage STAGE [--note N] [--force]` | Move a lead by hand (booked, won, lost ...). |

Exit codes everywhere: `0` ok, `1` the command ran but found a problem, `2` usage or
configuration error (one `error:` line on stderr; `-v` shows the traceback).

---

## Extending: your own adapter

Every station is a plug-in: a small class with `__init__(config, ctx)` (inherited) and one method.

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

Put the file in the repo folder, set `LEADGEN_PLUGINS=my_plugins` (in `.env`; several
modules separated by commas) and use `{type: tradeshow, url: ..., show: ...}` in a
playbook. Rules:

- All HTTP goes through `self.http`. It retries 429 / 5xx, masks keys in logs and counts
  every request in the usage summary. Never import `requests` in an adapter.
- Read keys with `self.secret()` at first use, never in `__init__`.
- Set `offline = True` only if the adapter never touches the network (dry runs only run
  offline adapters).
- An exporter that hands leads to a sending tool sets `scope = "outbound"`. It then runs
  only in outbound mode, only gets leads that may be emailed, and what it returns counts
  as handed over.
- Parse responses defensively (`.get(...)`).
- The `--budget` cap covers the built-in paid types (`registry.PAID`). Requests from your
  own adapter are counted, but not as paid lookups.

Tests use `tests.fakes.FakeHttp` with canned responses, so no test reaches the network.
Run the suite with `pip install -e ".[dev]" && pytest -q`.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `leadgen: command not found` | Activate the venv (`source .venv/bin/activate`, Windows `.venv\Scripts\activate`) or reinstall with `pip install -e .`. |
| `pip` says "externally-managed-environment" | Use a venv, as in the Quick start. |
| `error: client 'x' not found` | Run from the repo folder (the one with `clients/`), check `leadgen clients`, or pass `--clients-dir`. |
| `unknown setting '...' - did you mean ...?` | A typo in the client file; the message names the right key. |
| `[FAIL] ... missing credential: set $X` / doctor says `MISSING KEY` | Add `X=...` to `.env` in the folder you run from. Check with `leadgen doctor`. |
| Doctor says `failed ... key rejected` | The key is wrong, expired or lacks a permission; the detail names the provider's answer. |
| A dry run delivers 0 leads | `--dry-run` skips every source that uses the network. With the default playbook that is all of them. Run for real (Adzuna is free), or switch on the playbook's `csv` source to rehearse on your own file. |
| `Nothing to send this time` / `Low volume` | Most matches were already delivered, or the criteria are narrow. Read the top reasons and `_internal/not_delivered.csv`; widen `roles` / `locations`, raise `freshness_days`, or add sources. |
| Decision-maker / email columns are empty | The free setup can't name people. Switch on Hunter / Apollo / TheirStack, or add a `csv` finder with your own contact list (see [Your first real client](#your-first-real-client)). |
| Few `verified` emails | The free `basic` checker can't confirm mailboxes. Use a paid checker; guesses always stay `guessed-unverified`. |
| Files went to `<date>-2` | A delivery for that client and date already existed; nothing was overwritten. |
| `Paid-lookup budget reached` | Raise `budget.max_paid_lookups` in the client file or pass a higher `--budget`. |
| `... is an outbound-mode feature` | `replies` / `serve` / `followups` need `-p` with a `mode: outbound` playbook. |
| A client received a company twice | Different database files (`--db`, or clients on playbooks with different `storage.path`), or `redelivery_days` / `dedupe` allow it. |
| `leadgen suppress` didn't affect a delivery | The global list is per database file: add `-p <that client's playbook>` or `--db`, or use `--client NAME`. |
| Everything ends up in `rejected.csv` / `not_delivered.csv` | Read the reasons: usually `roles` are too narrow for the job titles found, `freshness_days` is short, or the location / size is too strict. |
| Need details | Add `-v` (info) or `-vv` (debug + full tracebacks). |

---

## Project layout

```
leadgen/
  cli.py            command line (leadgen ...)
  modes.py          delivery (default) / outbound
  pipeline.py       source -> signals -> ICP -> enrich -> verify -> score (-> write -> hand over in outbound)
  delivery/         client files, the ledger, rows + email labels, CSV / Excel / HTML / Sheets, opening lines, QA, deliver()
  usage.py          API usage metering, paid-lookup budget, cost estimates
  doctor.py         leadgen doctor: one free live check per API key
  playbook.py       playbook loading + every default setting (DEFAULTS)
  context.py        run context + the Adapter base class (keys, dry run, metered HTTP)
  registry.py       adapter type -> class; which types are paid / use-at-own-risk
  store.py          SQLite memory: runs, job history, leads, do-not-list, replies, follow-ups
  signals.py, filters.py, contacts.py, scoring.py   the pipeline stages
  replies.py, server.py, report.py                  replies + webhook server (outbound), reports
  sources/ enrich/ verify/ llm/ writer/ outbound/ notify/   the adapters
clients/            one YAML per client (_template.yaml, demo-client.yaml)
playbooks/          recruitment-delivery, demo-delivery, demo-offline, my-agency, templates/
examples/data/      fictional sample jobs, contacts and replies
deliveries/         the delivered files (per client, per date) - your clients' data, keep it private
data/               the databases (ledger, do-not-lists, history) - keep them safe
tests/              pytest suite (no network: HTTP is faked)
docs/               ARCHITECTURE.md (how it fits together), DELIVERY.md (delivery-layer contracts)
```
