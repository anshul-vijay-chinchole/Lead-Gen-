# CONTEXT - leadgen and the Hiring Signal Report business

One reference document for the repository `Lead-Gen-`: the Python command-line tool `leadgen` and the business built on it. It was assembled on 2026-09-25 from six section drafts. Each draft was written against the code on branch `claude/modest-hopper-lue9ct` and checked by running commands in a sandbox. Where two drafts disagreed, the code and the CLI were checked again, and the code wins.

## How to use this document

**Who it is for.**

- **The owner.** A college student learning Python who wants to start an agency and earn client money fast, with USD 100-200 to spend. The plain-English, practical parts are sections 1, 5 and 4.
- **A future AI assistant or developer** who needs the full context to keep working on the codebase: sections 2, 3 and 6, plus the appendices.

**Reading paths.**

| You are | Read in this order | Why |
|---|---|---|
| The owner | [1. Plain-English overview](#1-plain-english-overview), then [5. Business and agency plan](#5-business-and-agency-plan), then [4. Operations manual](#4-operations-manual) | What the product is, how to sell it, then how to run it every week |
| A developer or AI assistant | [2. Technical architecture](#2-technical-architecture), then [3. The stack](#3-the-stack-current-state-and-future-changes), then [6. Status, limitations and roadmap](#6-status-limitations-and-roadmap) | How it is built, what each part will need later, what is unfinished, and the rules you must not break ([6.10](#610-how-to-continue-this-project-for-an-ai-assistant-or-developer)) |
| Anyone looking something up | [Appendix A: file map](#appendix-a-file-map-quick-reference), [B: command cheat sheet](#appendix-b-command-cheat-sheet), [C: glossary](#appendix-c-glossary), [D: other documents](#appendix-d-other-documents-in-the-repository) | Quick reference |

**Where to find "the current stack, and the changes each part needs in the future".** It is covered at several levels, each from its own angle:

| Where | Angle |
|---|---|
| [Section 3](#3-the-stack-current-state-and-future-changes) (the whole section), especially [3.0](#30-stages-and-the-stack-at-a-glance) and [3.18](#318-priority-change-list) | Every part of the stack: what is used today, cost, limits, the changes needed at each stage, and one priority list |
| [2.2](#22-the-current-stack-and-what-each-part-will-need-in-the-future) | Each technical layer, how far it is proven, and pointers into section 3 |
| [1.7](#17-current-state-and-future-changes-overview-level) | Owner-level summary |
| [4.15](#415-operations-today-and-what-will-need-to-change) | Operations: what changes in the weekly routine, and when |
| [5.19](#519-current-stack-vs-business-needs-what-exists-today-and-what-each-will-need-later) | Each business step against the code that supports it today |
| [6.9](#69-roadmap) | The Now / Next / Later roadmap |

Section 3 uses four growth stages: **Stage 0** = now (pilots and the first 1-4 paying clients, USD 100-200), **Stage 1** = 5-10 clients, **Stage 2** = 20-50 clients or a first hire, **Stage 3** = selling the software itself. The roadmap's **Now / Next / Later** in 6.9 lines up roughly with Stage 0 / Stages 0-1 / Stages 1-3.

**How numbers are labelled.** Business numbers in this document carry one of these labels. None of them is researched market data.

| Label | Meaning |
|---|---|
| **Engine fact** | Checked in the code, or by running a command in this repository. |
| **Assumption** | A guess used to reason about the business. It is not researched market data. Replace it with real numbers as soon as you have them. |
| **Suggestion** | A starting point to test with the first clients (prices, volumes, wording). Change it freely. |
| **Target** | A goal to aim for. It is not a forecast or a promise. |
| **Check current pricing** | A third-party price that changes over time. Look it up on the provider's own website before relying on it. |

**The caveat that applies everywhere.** Outbound network access was blocked in the sandbox where this engine was built and reviewed. **No data or AI provider has ever been called live**: not Adzuna, TheirStack, Apollo, Apify, Hunter, MillionVerifier, ZeroBounce, NeverBounce, Anthropic, OpenAI, Google Sheets, Slack, Instantly or Smartlead. Every connector was written from the provider's documentation and tested against *canned responses* (made-up answers in the shape the provider documents). The first real call to each one is still to come. [6.6](#66-plan-to-go-live-safely) is the safe plan for it. Every company, person, email and domain quoted from a demo run comes from the repository's **made-up** sample data (`examples/data/`, domains ending in `-demo.com`).

**Conventions.** Commands are run from the repository root (`cd /home/user/Lead-Gen-` in the sandbox) with the `leadgen` command installed in editable mode (`pip install -e .`). The runs behind this document used scratch `--db` / `--out` paths, so the repository stayed clean. "Real output" means copied from an actual run. "Simulated" means the real CLI ran, but its web requests were answered by the test suite's fake HTTP client. Jargon is explained the first time it appears, and every term is also in [Appendix C](#appendix-c-glossary).

## Snapshot

| Item | Value |
|---|---|
| **Date** | 2026-09-25 |
| **Repository and branch** | `/home/user/Lead-Gen-`, branch `claude/modest-hopper-lue9ct`, last commit `03ec275` ("Foundation fixes from the delivery review"), 19 commits in total |
| **Code** | Python package `leadgen` version 0.1.0: about 25,900 lines in 73 files under `leadgen/`; command `leadgen` |
| **Tests** | 2,073 automated tests, all passing. Re-run while assembling this document: `2073 passed in 23.01s` on Python 3.11.15. Section 6 also ran the full suite on Python 3.9.23. No test touches the network. |
| **Default mode** | `delivery`: the engine makes lead files and never emails anyone. `outbound` (the old outreach engine) is kept and switched off by default. |
| **What is sold** | A weekly **Hiring Signal Report**: lead files (CSV + Excel + HTML) for small, specialised recruitment and staffing agencies. The owner does **not** email or run outreach for clients. |
| **Budget** | USD 100-200 to start. The default delivery path costs USD 0 in API fees (Adzuna free key, free email-pattern guesses, free offline email check). |
| **Live provider calls ever made** | None (the sandbox blocks outbound network) |
| **AI** | Off in client deliveries by default. Anthropic adapter default model `claude-opus-5`; `claude-haiku-4-5` is the cheapest model in the built-in price table |
| **Biggest limits today** | The free setup names no decision-makers (Adzuna returns companies and jobs, not people). A client in a new niche needs its own playbook copy. One SQLite file holds all history and must be backed up. |

## Table of contents

- [How to use this document](#how-to-use-this-document)
- [Snapshot](#snapshot)
- [1. Plain-English overview](#1-plain-english-overview)
  - [1.1 What leadgen is](#11-what-leadgen-is)
  - [1.2 The problem it solves, and for whom](#12-the-problem-it-solves-and-for-whom)
  - [1.3 What a client receives: a walk through the real demo output](#13-what-a-client-receives-a-walk-through-the-real-demo-output)
  - [1.4 The factory line: what happens inside, station by station](#14-the-factory-line-what-happens-inside-station-by-station)
  - [1.5 The two modes: delivery (on) and outbound (off)](#15-the-two-modes-delivery-on-and-outbound-off)
  - [1.6 What "niche-agnostic" means here (precisely)](#16-what-niche-agnostic-means-here-precisely)
  - [1.7 Current state and future changes (overview level)](#17-current-state-and-future-changes-overview-level)
- [2. Technical architecture](#2-technical-architecture)
  - [2.1 The big picture in plain English](#21-the-big-picture-in-plain-english)
  - [2.2 The current stack, and what each part will need in the future](#22-the-current-stack-and-what-each-part-will-need-in-the-future)
  - [2.3 Repository layout](#23-repository-layout)
  - [2.4 Data flow (ASCII diagram)](#24-data-flow-ascii-diagram)
  - [2.5 Data model (`leadgen/models.py`)](#25-data-model-leadgenmodelspy)
  - [2.6 Configuration: playbooks (`leadgen/playbook.py`)](#26-configuration-playbooks-leadgenplaybookpy)
  - [2.7 Modes and the outbound guard (`leadgen/modes.py`)](#27-modes-and-the-outbound-guard-leadgenmodespy)
  - [2.8 The pipeline, stage by stage (`leadgen/pipeline.py`)](#28-the-pipeline-stage-by-stage-leadgenpipelinepy)
  - [2.9 The delivery layer (`leadgen/delivery/`)](#29-the-delivery-layer-leadgendelivery)
  - [2.10 Storage (`leadgen/store.py`)](#210-storage-leadgenstorepy)
  - [2.11 Usage metering, the budget and prices (`leadgen/usage.py`)](#211-usage-metering-the-budget-and-prices-leadgenusagepy)
  - [2.12 HTTP layer (`leadgen/http.py`)](#212-http-layer-leadgenhttppy)
  - [2.13 Adapter registry and plugins (`leadgen/registry.py`)](#213-adapter-registry-and-plugins-leadgenregistrypy)
  - [2.14 `leadgen doctor` (`leadgen/doctor.py`)](#214-leadgen-doctor-leadgendoctorpy)
  - [2.15 Outbound-only parts: replies and the webhook server](#215-outbound-only-parts-replies-and-the-webhook-server)
  - [2.16 Command-line surface (`leadgen/cli.py`)](#216-command-line-surface-leadgenclipy)
  - [2.17 Security and privacy measures](#217-security-and-privacy-measures)
  - [2.18 Testing strategy (`tests/`)](#218-testing-strategy-tests)
  - [2.19 Extension guide: writing your own adapter](#219-extension-guide-writing-your-own-adapter)
  - [2.20 Known gaps, inconsistencies and things never verified live](#220-known-gaps-inconsistencies-and-things-never-verified-live)
- [3. The stack: current state and future changes](#3-the-stack-current-state-and-future-changes)
  - [3.0 Stages and the stack at a glance](#30-stages-and-the-stack-at-a-glance)
  - [3.1 Language and runtime](#31-language-and-runtime)
  - [3.2 Core libraries](#32-core-libraries)
  - [3.3 CLI (command-line interface)](#33-cli-command-line-interface)
  - [3.4 Configuration](#34-configuration)
  - [3.5 Storage (SQLite store and ledger)](#35-storage-sqlite-store-and-ledger)
  - [3.6 Job-signal sources](#36-job-signal-sources)
  - [3.7 Contact finders](#37-contact-finders)
  - [3.8 Email verification](#38-email-verification)
  - [3.9 AI (large language models)](#39-ai-large-language-models)
  - [3.10 Output and delivery formats](#310-output-and-delivery-formats)
  - [3.11 Outbound tools (kept for later)](#311-outbound-tools-kept-for-later)
  - [3.12 Scheduling and automation](#312-scheduling-and-automation)
  - [3.13 Hosting](#313-hosting)
  - [3.14 Monitoring and QA](#314-monitoring-and-qa)
  - [3.15 Testing and CI](#315-testing-and-ci)
  - [3.16 Security and compliance](#316-security-and-compliance)
  - [3.17 Business tooling (not in the repo)](#317-business-tooling-not-in-the-repo)
  - [3.18 Priority change list](#318-priority-change-list)
- [4. Operations manual](#4-operations-manual)
  - [4.1 What you need before you start](#41-what-you-need-before-you-start)
  - [4.2 Installation](#42-installation)
  - [4.3 The `.env` file: every variable](#43-the-env-file-every-variable)
  - [4.4 First run: the offline demo client](#44-first-run-the-offline-demo-client)
  - [4.5 Setting up a real client](#45-setting-up-a-real-client)
  - [4.6 The weekly routine](#46-the-weekly-routine)
  - [4.7 Handling the QA summary](#47-handling-the-qa-summary)
  - [4.8 Managing do-not-lists](#48-managing-do-not-lists)
  - [4.9 Budget and cost control](#49-budget-and-cost-control)
  - [4.10 Automation](#410-automation)
  - [4.11 Backing up the ledger database](#411-backing-up-the-ledger-database)
  - [4.12 Command reference](#412-command-reference)
  - [4.13 Outbound mode operations (brief)](#413-outbound-mode-operations-brief)
  - [4.14 Troubleshooting](#414-troubleshooting)
  - [4.15 Operations today, and what will need to change](#415-operations-today-and-what-will-need-to-change)
  - [4.16 What was verified, and what was not](#416-what-was-verified-and-what-was-not)
- [5. Business and agency plan](#5-business-and-agency-plan)
  - [5.1 The business in one paragraph](#51-the-business-in-one-paragraph)
  - [5.2 The problem chain: why an agency would pay](#52-the-problem-chain-why-an-agency-would-pay)
  - [5.3 Ideal customer profile (ICP)](#53-ideal-customer-profile-icp)
  - [5.4 What the client receives](#54-what-the-client-receives)
  - [5.5 What the problem is worth (value maths)](#55-what-the-problem-is-worth-value-maths)
  - [5.6 The offer ladder and suggested prices](#56-the-offer-ladder-and-suggested-prices)
  - [5.7 Unit economics per client](#57-unit-economics-per-client)
  - [5.8 Budget allocation for USD 100-200](#58-budget-allocation-for-usd-100-200)
  - [5.9 Go-to-market: how to get the first clients](#59-go-to-market-how-to-get-the-first-clients)
  - [5.10 Closing and payment](#510-closing-and-payment)
  - [5.11 Onboarding checklist for a new client](#511-onboarding-checklist-for-a-new-client)
  - [5.12 Delivery SLA and quality promises](#512-delivery-sla-and-quality-promises)
  - [5.13 The weekly routine (short version)](#513-the-weekly-routine-short-version)
  - [5.14 Retention and upsell](#514-retention-and-upsell)
  - [5.15 KPIs and the tracking sheet](#515-kpis-and-the-tracking-sheet)
  - [5.16 The 7-day, 30-day and 90-day plan](#516-the-7-day-30-day-and-90-day-plan)
  - [5.17 Scaling paths](#517-scaling-paths)
  - [5.18 Risks and compliance](#518-risks-and-compliance)
  - [5.19 Current stack vs business needs: what exists today and what each will need later](#519-current-stack-vs-business-needs-what-exists-today-and-what-each-will-need-later)
  - [5.20 The pitch in 30 seconds](#520-the-pitch-in-30-seconds)
- [6. Status, limitations and roadmap](#6-status-limitations-and-roadmap)
  - [6.1 The short version (for the owner)](#61-the-short-version-for-the-owner)
  - [6.2 Snapshot at a glance](#62-snapshot-at-a-glance)
  - [6.3 What is built and working (verified)](#63-what-is-built-and-working-verified)
  - [6.4 Switched off but kept](#64-switched-off-but-kept)
  - [6.5 Never tested live: every network adapter](#65-never-tested-live-every-network-adapter)
  - [6.6 Plan to go live safely](#66-plan-to-go-live-safely)
  - [6.7 Known limitations](#67-known-limitations)
  - [6.8 Project history](#68-project-history)
  - [6.9 Roadmap](#69-roadmap)
  - [6.10 How to continue this project (for an AI assistant or developer)](#610-how-to-continue-this-project-for-an-ai-assistant-or-developer)
- [Appendix A. File map (quick reference)](#appendix-a-file-map-quick-reference)
- [Appendix B. Command cheat sheet](#appendix-b-command-cheat-sheet)
- [Appendix C. Glossary](#appendix-c-glossary)
- [Appendix D. Other documents in the repository](#appendix-d-other-documents-in-the-repository)

---

## 1. Plain-English overview

> **How this section was checked.** Everything below was read from the code on branch
> `claude/modest-hopper-lue9ct` and from real runs made on 2026-09-25 in a sandbox:
> `leadgen deliver --client demo-client` (three times, into scratch folders), a `--dry-run`,
> `leadgen adapters`, and the full test suite (`2073 passed in 28.25s`). The sandbox has no
> outbound internet, so **no data provider (Adzuna, Apollo, Hunter, the email checkers, AI,
> Google Sheets, Slack) has ever been called live**. Every connector was built from the
> provider's documentation and tested against canned (pre-written, fake) responses.
> Every company, person, email and domain quoted in this section comes from the repo's
> **made-up** sample data (`examples/data/`).

Jargon is explained the first time it appears, and every term is also in [Appendix C: Glossary](#appendix-c-glossary).

### 1.1 What leadgen is

`leadgen` is a Python command-line tool (you type commands such as `leadgen deliver --client acme`
in a terminal) that produces a weekly **Hiring Signal Report** for recruitment and staffing
agencies. For each client agency it finds companies that posted a job **in the last few days**
for the roles that agency fills. It then finds the person most likely to own that hire, checks
that person's email and labels it honestly, and gives each lead a 0-100 score. Finally it writes a
CSV, an Excel workbook and a one-page HTML summary that you (the owner) send to the client. It
remembers what each client already received, so a client never gets the same company, job or
person twice. It **never emails, calls or messages anyone**: you sell the file, and the agency
does its own outreach. The default setup costs nothing to run. Paid data tools are built in
but switched off.

### 1.2 The problem it solves, and for whom

**Who buys it.** Small, specialised recruitment and staffing agencies, for example a firm
that places accountants in Texas, or nurses in one region. They earn a fee when they fill a
job. To do that, they first need to know **which companies are hiring right now** and **who at
that company to call**.

**What it replaces.** Without the report, a recruiter trawls job boards, works out which ads
are new and which are old re-posts, looks up the company, finds the hiring manager on
LinkedIn, and guesses an email. That can take hours every week, and it is easy to end up
contacting the same companies again (a plausible picture, not measured).

**Why an agency would pay** (the product's pitch, from `PLAN.md`; not market research):

| Promise | How the code delivers it |
|---|---|
| **Timing.** A job posted yesterday is a live need, and the first agency to call has an edge. | Only jobs posted within `freshness_days` (default **7**) count. Undated jobs and re-posted old ads are left out by default (`leadgen/signals.py`). |
| **Time saved.** One file replaces the manual research. | One command per client runs the whole chain (`leadgen/delivery/run.py`, `deliver()`). |
| **Nothing twice.** Every file holds only new leads. | The per-client **ledger** records every delivered company, job and person and removes them next time (`leadgen/delivery/ledger.py`). |
| **No surprises about emails.** | Every email carries exactly one of four labels: `verified`, `risky`, `guessed-unverified`, `not found` (`leadgen/delivery/rows.py`). A guessed address is never called verified. |

**Who runs it.** You, the owner. The weekly routine is three commands per client:
`leadgen clients` (who is due), `leadgen doctor --client NAME` (are the API keys still working;
it uses free endpoints only), and `leadgen deliver --client NAME`. After that you read the QA
summary, spot-check a few rows, and send the files. leadgen never sends the report for you.

**What the free setup can and cannot do.** This matters on a USD 100-200 budget. The default
live playbook (`playbooks/recruitment-delivery.yaml`) is free: Adzuna job search (free API
key), free email-pattern guesses, and a free offline email check. But Adzuna returns the
company, job, link, date and location, **not people**. So on the free setup alone, the
decision-maker and email columns are mostly empty (`not found`). To name decision-makers you
need a contact list you are allowed to use (the `csv` finder), or a paid tool (Hunter, Apollo
or TheirStack), capped with `--budget`. The README says this in its "Know what the free setup
gives you" box.

### 1.3 What a client receives: a walk through the real demo output

The demo client `clients/demo-client.yaml` is a made-up agency, **Northstar Finance
Recruiting**. It places accountants, controllers, financial analysts and payroll staff in
Texas, Illinois, New York, Georgia and the UK, and wants **10 leads a week**. Its base playbook,
`playbooks/demo-delivery.yaml`, runs **100% offline**: jobs come from
`examples/data/demo_jobs.csv` (40 made-up job rows) and people from
`examples/data/demo_delivery_contacts.csv`. The sample dates are written as "2 days ago", so
the demo never goes out of date.

The command used for this section (scratch paths, so the repo stays clean):

```bash
cd /home/user/Lead-Gen-
SCRATCH=/path/to/a/scratch/folder    # any folder outside the repository
leadgen deliver --client demo-client --db "$SCRATCH/o.db" --out "$SCRATCH/out"
```

Without `--db` / `--out`, the files go to `deliveries/demo-client/<today>/` and the memory to
`data/demo-delivery.db`. Because `--db` pointed somewhere other than the playbook's database,
the run ended with a `Careful: this delivery used --db ...` warning. That warning is
deliberate: it stops you sending files from a rehearsal database. The command exited with code
`0` (delivered).

**What lands in the folder:**

```
out/
  demo-client-hiring-signals-2026-09-25.csv    <- for the client
  demo-client-hiring-signals-2026-09-25.xlsx   <- for the client
  demo-client-hiring-signals-2026-09-25.html   <- for the client
  _internal/                                   <- yours, never send it
    qa.txt, qa.json                            the QA summary (text + data)
    not_delivered.csv                          every company / lead left out, and why
    20260925-064110-e0ab64/                    the pipeline run (the run ID changes each time)
      opportunities.csv                        every lead with its score and score reasons
      rejected.csv                             every company rejected by the pipeline, and why
      summary.json                             counts, errors, warnings, top leads
```

File names always follow `<client>-hiring-signals-<YYYY-MM-DD>.<ext>`. A dry run adds
`-PREVIEW` (see 1.3.6).

#### 1.3.1 The CSV (the main product)

One row per company, best first: `hot` before `normal`, then by score. The demo file has
**10 rows and 19 columns**. The first 18 columns are always there. The 19th, "Suggested
opening line", appears only when the client file sets `opening_line.enabled: true` (the demo
does; the default is off). The file is UTF-8 with a BOM (a hidden marker at the start that
makes Excel show accents correctly). Any cell starting with `=`, `+`, `-` or `@` gets a leading
`'`, so a spreadsheet never runs it as a formula (`guard_cell`).

The real header and the first real row (made-up data):

```csv
Company,Website,Company size,Industry,Location,Signal type,Job title(s),Job link,Date posted,Posted,Urgency,Score,Decision-maker,Decision-maker title,LinkedIn URL,Email,Email status,Source,Suggested opening line
Hudson Yards Media,https://hudsonyardsmedia-demo.com,340,Media,"New York, NY",Hiring,FP&A Manager; Senior Financial Analyst,https://hudsonyardsmedia-demo.com/jobs/fpa-manager,2026-09-23,posted 2 days ago,hot,78,Nathan Cole,Chief Financial Officer,https://linkedin-demo.com/in/nathan-cole,nathan.cole@hudsonyardsmedia-demo.com,verified,demo-jobs; contact via csv,"Saw Hudson Yards Media is hiring for 2 roles, including an FP&A Manager in New York, NY (posted 2 days ago)."
```

What each column means (the order and headers come from `BASE_COLUMNS` in
`leadgen/delivery/rows.py`):

| # | Column | What it holds | Demo example |
|---|---|---|---|
| 1 | Company | The hiring company. | Hudson Yards Media |
| 2 | Website | The company site (built from the domain if the source gave only a domain). Empty when unknown. | https://hudsonyardsmedia-demo.com |
| 3 | Company size | Head-count if a source reported it. | 340 |
| 4 | Industry | If known. | Media |
| 5 | Location | The company's location (or country, or the job's location as a fallback). | New York, NY |
| 6 | Signal type | Why the company is on the list now. `Hiring` = a live job posting. | Hiring |
| 7 | Job title(s) | Matching open roles, freshest first, up to 5, then "(+N more)". Separated by `; `. | FP&A Manager; Senior Financial Analyst |
| 8 | Job link | The ad for the top (freshest) job. | .../jobs/fpa-manager |
| 9 | Date posted | ISO date (YYYY-MM-DD). | 2026-09-23 |
| 10 | Posted | Human wording as of the delivery date: `posted today`, `posted 1 day ago`, `posted N days ago`, or `date unknown (first seen ...)` when undated jobs are allowed. | posted 2 days ago |
| 11 | Urgency | The tier: `hot` or `normal` (`skip` only if the client allows it). | hot |
| 12 | Score | 0-100. | 78 |
| 13 | Decision-maker | The person picked from the client's `buyer_titles` (best title first). One per company. | (made-up name) |
| 14 | Decision-maker title | Their job title. | Chief Financial Officer |
| 15 | LinkedIn URL | If found. | (made-up profile link) |
| 16 | Email | The address, unless its label is `not found`. | (made-up address) |
| 17 | Email status | Exactly one of `verified`, `risky`, `guessed-unverified`, `not found`. | verified |
| 18 | Source | Where the lead came from, plus `contact via <finder>` for the person. | demo-jobs; contact via csv |
| 19 | Suggested opening line | Optional. A factual first line the recruiter can adapt. | "Saw Hudson Yards Media is hiring for 2 roles, ..." |

All ten demo rows, shortened (names left out):

| Company | Job title(s) | Posted | Urgency | Score | Decision-maker title | Email status |
|---|---|---|---|---|---|---|
| Hudson Yards Media | FP&A Manager; Senior Financial Analyst | posted 2 days ago | hot | 78 | Chief Financial Officer | verified |
| Lonestar Freight Co | Senior Accountant; Accounts Payable Specialist | posted 1 day ago | hot | 73 | CFO | verified |
| Kingsbridge Logistics Ltd | Management Accountant; Credit Controller | posted 1 day ago | hot | 70 | Finance Director | verified |
| Lakeshore Manufacturing | Cost Accountant; Plant Controller | posted 2 days ago | hot | 70 | VP Finance | verified |
| Riverbend Health Partners | Payroll Manager; Revenue Cycle Accountant | posted 2 days ago | normal | 60 | Controller | risky |
| Pecan Street Software | Financial Analyst | posted today | normal | 59 | VP of Finance | risky |
| Magnolia Home Goods | Accounting Manager | posted 1 day ago | normal | 57 | Head of Finance | guessed-unverified |
| Prairie Mutual Insurance | Senior Financial Analyst | posted 4 days ago | normal | 56 | Controller | verified |
| Northern Quarter Games | Finance Manager | posted 4 days ago | normal | 48 | Head of Finance | risky |
| Clydeside Engineering Ltd | Assistant Accountant | posted 2 days ago | normal | 46 | Financial Controller | not found |

**Why each email label appears in the demo.** The demo checker is the free `basic` one, which
cannot confirm any mailbox. So the labels come from what the sample contact list says, and
from how the address was obtained:

| Label | Demo case | Why |
|---|---|---|
| `verified` | Hudson Yards, Lonestar, Kingsbridge, Lakeshore, Prairie Mutual | The contact list marks the address `verified`, and it was not built from a pattern. |
| `risky` | Riverbend (list says `catch-all`), Pecan Street and Northern Quarter (not confirmed) | A real address from a list, but not confirmed. |
| `guessed-unverified` | Magnolia Home Goods | The list names the person but has no email, so the free `pattern` finder built one from a name pattern. A guess is **always** this label, whatever a checker says. |
| `not found` | Clydeside Engineering | The company has no website/domain, so no address could be guessed. The row still has the person's name, title and LinkedIn URL. |

#### 1.3.2 The Excel workbook (`.xlsx`)

Two sheets (checked by opening the real file with `openpyxl`):

- **Leads.** The same 19 columns and 10 rows as the CSV, with a bold header in the brand
  colour (`#1f4e79` in the demo), the header row frozen, an auto-filter on every column,
  clickable links (Website, Job link, LinkedIn URL) and real Excel dates (`yyyy-mm-dd`).
- **About.** A cover sheet for the client. Real content from the demo:

```
Hiring Signal Report
Prepared for          Northstar Finance Recruiting
Period                2026-09-18 to 2026-09-25
Delivered on          2026-09-25
Leads in this file    10
Hot leads             4
Companies             10
Notes                 Jobs posted in the last 7 days (as of 2026-09-25).
                      Jobs without a posting date are left out.
                      Re-posted (stale) job ads are left out.
                      Companies, jobs and contacts already sent to you in earlier deliveries are left out.
Leads by signal       Hiring 10
Leads by email status verified 5, risky 3, guessed-unverified 1, not found 1
Email status legend   (one line per label - see below)
Columns               (a plain-English guide to 8 key columns)
Prepared by           <sender name> | <sender email> | <website>, then the playbook's footer
```

The legend wording the client sees (quoted from the file):

- **verified**: "Mailbox confirmed deliverable by an email checker or by the data provider that
  supplied it. Never a guessed address."
- **risky**: "A real address from a data provider that could not be confirmed (for example the
  company's mail server accepts every address). Usually works; expect some bounces."
- **guessed-unverified**: "Built from the company's usual name pattern (e.g.
  first.last@company.com). Not confirmed - check it before you rely on it."
- **not found**: "No usable email for this person in this file. Use the LinkedIn URL or the
  company website to reach them."

#### 1.3.3 The HTML summary (`.html`)

A single self-contained web page (about 11 KB, styles inline, no external files) that opens in
any browser and prints cleanly to PDF. Its browser title is
`Hiring Signal Report - Northstar Finance Recruiting - 2026-09-25`. From top to bottom (visible
text taken from the real file):

1. **Header:** "Hiring Signal Report", "Prepared for Northstar Finance Recruiting",
   "18 Sep 2026 – 25 Sep 2026".
2. **Four headline tiles:** `10` Leads delivered, `4` Hot leads, `5` Verified emails,
   `10` Companies.
3. **Leads by signal type:** `Hiring | 10 | 100%`.
4. **Lead table:** headed "All 10 leads, hottest first" (with more than 10 rows it becomes
   "Top 10 hottest leads"). Columns: Company, Role(s), Posted, Location, Urgency,
   Decision-maker (name + title), Email status. It shows **no email addresses**. Below the
   table: "Emails and LinkedIn profiles are in the Excel / CSV file." This makes the HTML the
   safe page to forward or print.
5. **Email status legend** (same wording as the Excel file).
6. **About this report:** the same four notes as the Excel About sheet.
7. **Footer:** "Prepared by" with the sender's name, email and website, plus the playbook footer.

The brand name, colour, logo, sender and footer come from the playbook's `delivery:` section.
A client file can change them under `branding:`.

#### 1.3.4 The QA summary (for you, not the client)

QA means quality assurance: a check before you send anything. It is printed after every
delivery and saved as `_internal/qa.txt` (and as data in `qa.json`). The real demo output:

```
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
  on a do-not-list ........... 3
  held back (over the limit) . 2 - not recorded, so they can go in a later delivery
  verified email rate ........ 50% (5 of 10)
  email status ............... verified 5, risky 3, guessed-unverified 1, not found 1
  opening lines .............. 0 AI, 10 template
  API usage: paid lookups 0 (no cap)
    no API calls (free / offline run)
```

How to read the demo numbers:

| Line | Demo value | What happened |
|---|---|---|
| companies found | 25 | Distinct companies in the sample job file, after merging duplicates. |
| with a live signal | 21 | 4 dropped at the signal step: one had only jobs older than 7 days, one had an undated job, one had only an intern job (`exclude_roles`), one had jobs that did not match the client's `roles`. |
| match the client's criteria | 14 | 7 dropped: 4 in places the client doesn't cover (e.g. Toronto, Sydney), 1 too small (`too small (12 employees, min 20)`), 1 on the client's excluded domain list, 1 a competitor (`staffing` in its name). |
| (not shown as its own line) | 13 go on | 1 more removed **before any lookup** because it is on the client's own do-not-list (`exclusions.companies`: already their client). |
| delivered | 10 (target 10), 4 hot | Of the 13 scored leads, 1 had funding news but no job, so it was dropped ("no live job posting"). That left 12: 10 went in the file and 2 were held back. |
| filtered out | 13 | 4 (signals) + 7 (criteria) + 1 (client do-not-list) + 1 (no live job). |
| on a do-not-list | 3 | The client file's three kinds of exclusion: a company name, a domain, a keyword. These are also counted in "filtered out". |
| held back | 2 | Good leads over the weekly limit. **Not recorded** in the ledger, so they can arrive in a later delivery if they still qualify (still fresh, still among the best). |
| verified email rate | 50% | 5 of 10 rows are `verified`. This comes from the made-up demo contacts; on the free default path expect about 0% (see 4.7.4). |
| API usage | 0 paid lookups | Nothing costs money in the demo. |

**Warnings** are added when there are 0 leads, volume is below target, the paid-lookup budget
ran out, a source failed, the AI cost cap was hit, or a Google Sheets push failed. Exit codes:
`0` delivered, `1` nothing delivered (or every source failed), `2` a problem with the command
or client file.

#### 1.3.5 The other internal files

`_internal/not_delivered.csv` lists every company or lead left out, with the stage and the
exact reason. Three real lines from the demo (the made-up person's name replaced by `<name>`):

```csv
company,domain,contact,stage,reason
Alamo Craft Brewing,alamobrewing-demo.com,,icp,"too small (12 employees, min 20)"
Riverwalk Hospitality Group,riverwalk-demo.com,,delivery,on this client's do-not-list (company name)
Peachtree Dental Group,peachtreedental-demo.com,<name>,selection,over this client's weekly limit of 10 leads (not recorded - it can go in a later delivery)
```

The run folder's `opportunities.csv` explains every score. The real reasons for Hudson Yards
Media: `fresh signal (2d); 2 matching signals; location match; size match; industry match;
Chief Financial Officer = buyer #1; email valid; +funding`.

#### 1.3.6 Run it again: the ledger at work

Running the same command again on the same database (as if a week had passed, in a new
output folder):

```
  delivered .................. 2 (target 10), 0 hot
  filtered out ............... 23
    top reasons:
      10  already delivered to this client
  ...
  duplicates removed ......... 10 (already delivered: 10 companies)
  WARNING: Low volume: 2 of 10 leads delivered (below this client's weekly target). Most matches
  were already delivered to this client in earlier weeks. To find more, widen the roles / locations,
  raise freshness_days or add sources.
```

Only the 2 held-back companies (Peachtree Dental Group, Empire Solar) came through. A third run
delivered 0, printed "Nothing to send this time: no new leads for Northstar Finance Recruiting",
warned "No leads in this delivery - the files are empty. Don't send them to the client", and
exited with code `1`.

A **preview** (`--dry-run`) wrote `demo-client-hiring-signals-2026-09-25-PREVIEW.csv` / `.xlsx` /
`.html`. It was labelled "PREVIEW files - not for the client (nothing was recorded as
delivered)" and recorded nothing in the ledger.

### 1.4 The factory line: what happens inside, station by station

Think of `leadgen deliver` as a factory line. Raw material (job ads) goes in one end, and a
checked, labelled lead file comes out the other. Each station removes or adds something. The
order matters: everything that can be thrown away for free is thrown away **before** any
station that might cost money.

| # | Station (plain English) | What it does | Where in the code | Settings that steer it | Can cost money? |
|---|---|---|---|---|---|
| 0 | **Read the order** | Loads the client file and its base playbook, and merges them. The client's targeting sits on top. The run is always forced to `mode: delivery`. | `delivery/client.py` (`load_client`, `client_playbook`) | the whole client file | No |
| 1 | **Find** | Each switched-on source fetches companies with their job ads. The same company from two sources is merged into one. | `pipeline.py` stage 1; `sources/*` | playbook `sources:` | Only paid sources (TheirStack, Apollo, Apify) |
| 2 | **Freshness and role match** | Keeps jobs whose **title** names one of the client's `roles`, drops `exclude_roles`, keeps only jobs posted in the last `freshness_days` (default 7). Drops undated jobs (`allow_undated: false`) and re-posted old ads (`drop_reposts: true`). A company left with no matching job is dropped. | `signals.py` (`process_signals`) | `roles`, `exclude_roles`, `freshness_days`, `allow_undated`, `drop_reposts` | No |
| 3 | **Filter (client criteria)** | Location, company size, industry, excluded domains and keywords, and the global do-not-list. | `filters.py` (`apply_icp`) | `locations`, `company_size`, `industries`, `exclusions` | No |
| 4 | **Already delivered?** | Removes companies and jobs this client already received, and companies on the client's own do-not-list. This runs **before any paid lookup**, so no money is spent on leads that can't go out. | `delivery/ledger.py` (`LedgerHooks.filter_company`), called at `pipeline.py` stage 3b | `dedupe`, `redelivery_days`, `exclusions.companies` | No |
| 4b | *(Rank before spending)* | Every remaining company gets a provisional score. Only the best `max(leads_per_week x 2, 10)` are looked up in the next station (fewer if the base playbook's `enrichment.max_companies` is lower: it is 100 in `recruitment-delivery.yaml` and 50 in `demo-delivery.yaml`). | `pipeline.py` stage 4 | `enrichment.max_companies` | No |
| 5 | **Find the decision-maker** | Contact finders run in order until a person with a wanted title **and** a usable email (or email guesses to check) is found (a "waterfall"). People already delivered to this client, or on a do-not-list, are skipped as soon as they appear. One person per company. | `contacts.py` (`ContactWaterfall`, `select_contacts`); `enrich/*` | `buyer_titles`, playbook `enrichment.finders` | Only paid finders (Apollo, Hunter) |
| 6 | **Check the email** | The verifier checks the chosen person's address and returns valid / risky / invalid / unknown. Results are cached for 30 days. If the budget runs out, a paid checker falls back to the free `basic` one. | `pipeline.py` (`verify_contact`); `verify/*` | playbook `enrichment.verifier` | Only paid checkers |
| 7 | **Score** | 0-100 from four parts: intent (fresh, several roles, hard to fill, urgent wording), fit, reachability (title rank + email quality), extra signals (e.g. funding). Then a tier: in `recruitment-delivery.yaml` and `demo-delivery.yaml` `hot` >= 65, `normal` >= 15, else `skip` (`templates/generic.yaml` and the engine default use 80 / 60). | `scoring.py` (`score`, `tier_for`) | playbook `scoring:` | No |
| 8 | **Pick what goes in the file** | Keeps only the client's `tiers`, and only leads with a live job posting. One row per company and per person. Hot first, then by score. Cut to `leads_per_week`. The rest are "held back" for next time. | `delivery/run.py` (`select_leads`) | `tiers`, `leads_per_week` | No |
| 9 | **Label and write the files** | Builds each row, including the honest email label and the client's email policy. Optionally adds an opening line. Writes CSV / Excel / HTML, and optionally pushes to Google Sheets (never in a dry run). | `delivery/rows.py` (`build_row`, `email_label`); `delivery/opening.py`; `delivery/formats.py` | `emails.include_unverified`, `opening_line`, `delivery.formats`, `delivery.google_sheet` | Only AI opening lines (capped by `opening_line.max_cost_usd`, default 0.50) |
| 10 | **Record in the ledger** | Stores every delivered company, job and person for this client, so they never come back. Skipped in a dry run. Held-back leads are not recorded. | `delivery/ledger.py` (`Ledger.record`) | `dedupe`, `redelivery_days` | No |
| 11 | **QA** | Prints and saves the QA summary, `not_delivered.csv` and the usage/cost lines. | `delivery/qa.py` (`build_qa`) | - | No |

Two safety rules apply along the whole line. **One broken part does not stop the run**: a
failing source or a missing key is logged and the run carries on with what it has. And
**`--budget N` stops paid requests before they reach the network**. Once the cap is hit, paid
sources and finders stop and free tools keep working.

### 1.5 The two modes: delivery (on) and outbound (off)

Every playbook runs in one **mode** (`mode:` in the YAML; the default is `delivery`; defined in
`leadgen/modes.py`).

| | `delivery` (default, the product) | `outbound` (kept, off by default) |
|---|---|---|
| What it's for | Selling lead files to agencies. | Doing cold-email outreach **yourself**: the engine's original job. |
| Pipeline | find -> signals -> filter -> find person -> check email -> score -> files | the same, **plus** writing email sequences (template or AI) and handing leads to a sending tool |
| Email writing | Never: the writer is not even built. | Yes (`leadgen/writer/*`). |
| Hand-over to sending tools | Never built (Instantly, Smartlead, webhook exporters are stripped). | Yes: upload CSVs or direct push (`leadgen/outbound/*`). |
| Replies, follow-ups, webhook server | `leadgen replies` / `serve` / `followups` refuse to run (exit 2). | Work (`leadgen/replies.py`, `leadgen/server.py`). |
| Shipped playbooks | `recruitment-delivery.yaml`, `demo-delivery.yaml`, `templates/generic.yaml` | `my-agency.yaml`, `demo-offline.yaml`, `templates/recruitment.yaml`, `saas-funding.yaml`, `local-business.yaml`, `agency-outreach.yaml` |

**Why outbound is switched off for clients.** The business sells files; it does not run
outreach for anyone (`PLAN.md`: "We don't email, call or message anyone ... The agency does its
own outreach. The engine enforces this"). The code enforces it in layers, so a client delivery
can never send, write copy or process replies by accident: the default mode is `delivery`
(`playbook.py` `DEFAULTS`); `client_playbook()` **forces** `mode: delivery` for every client
delivery and removes hand-over exporters, whatever the base playbook says; the pipeline checks
the mode before the WRITE and hand-over stages; and `require_outbound()` plus the CLI's gate refuse
`replies` / `serve` / `followups` before opening a database or the network. Every guard, with
where it lives in the code, is listed in [2.7](#27-modes-and-the-outbound-guard-leadgenmodespy).

Outbound also needs things a small budget and a file-selling business don't: a sending tool,
warmed-up inboxes on separate domains, usually paid data and AI keys, and responsibility for
cold-email rules wherever the recipients are (all from the README's outbound section).

**Where outbound is still used.** `playbooks/my-agency.yaml` is an outbound playbook for
finding **your own** clients (it targets B2B service firms, including recruitment agencies,
that are hiring sales / business-development staff). Two caveats: its `offer:` section still
pitches "outbound lead generation and AI automation", so rewrite it to pitch the Hiring Signal
Report first. And as shipped it relies on paid keys (its comments list TheirStack, Apollo,
Hunter, MillionVerifier and Anthropic). It can be switched to `mode: delivery` if you only
want the list of prospects.

> **Doc inconsistency to resolve.** `PLAN.md` section 1 says "We don't email, call or message
> anyone, for ourselves or for our clients", while `PLAN.md` section 5, the README and
> `my-agency.yaml` describe using outbound mode for your own prospecting. The code supports
> both. The product (client deliveries) never does outreach. Whether the owner uses outbound
> for his own sales is a business choice, and the "for ourselves" wording should be updated to
> match. It is listed with the other documentation drift in [6.7](#67-known-limitations).

### 1.6 What "niche-agnostic" means here (precisely)

`docs/ARCHITECTURE.md` says the engine is "niche-agnostic and country-agnostic: nothing about
a niche, a country or a legal regime is hard-coded". That is true of the **engine**, but only
partly true of the **client report**. Here is the difference.

**The engine: yes, niche-agnostic.**
- Signals are generic. `models.py` defines 12 types (`job_posting`, `funding`,
  `leadership_change`, `expansion`, `headcount_growth`, `tech_adoption`, `news`, `review`,
  `ad_activity`, `website_change`, `event`, `custom`), and any other string is allowed too.
- The playbook picks which signal drives the score (`signals.primary`). The shipped templates
  show other niches: `saas-funding.yaml` (primary `funding`), `local-business.yaml` (primary
  `review`, from Google Maps via Apify), `agency-outreach.yaml`.
- Sources, finders, checkers and exporters are plug-ins, and `csv` / `json` sources accept
  anyone's data. `leadgen run -p <playbook>` runs any playbook without a client and writes a
  review file (`opportunities.csv`).
- No country's law is built in, and locations are plain text matches.

**The client delivery (`leadgen deliver`) and its files: built for hiring signals.**
- `ensure_job_postings_primary()` always adds `job_posting` as a primary signal type.
- `select_leads()` drops any lead without a live job posting (or another signal type the
  playbook marks as primary), with the reason "no live job posting (only other signals, e.g.
  funding news)". Non-primary signals such as funding only add bonus points.
- Fixed wording: the column headers `Job title(s)`, `Job link`, `Date posted`; file names
  `<client>-hiring-signals-<date>`; the About/HTML notes "Jobs posted in the last N days";
  the AI opening-line prompt ("You help recruitment agencies start conversations with
  companies that are hiring"); and client-file keys such as `roles`.
- Configurable: the report name (`delivery.brand_name`, default "Hiring Signal Report"),
  colours, logo, sender and footer. The signal labels (`Funding`, `Expansion`, ...) and the
  free template opening lines ("Saw the recent funding news at ...") already handle
  non-hiring signals.

**Within recruitment: any niche, with one manual step.** A nursing or driver recruiter works
the same way. The client's `roles` decide which jobs **count**. But the source search words
(Adzuna `queries`, TheirStack `job_titles`) decide what is **fetched**, and they live in the
playbook, not the client file. So a client in a new niche needs its own copy of
`recruitment-delivery.yaml` with new search words. `PLAN.md` lists automating this as future
work.

### 1.7 Current state and future changes (overview level)

| Area | Where it stands today | Change needed later |
|---|---|---|
| Live providers | Built from the providers' docs; tested only against canned responses; never called live. | For each key: `leadgen doctor`, then a small real delivery (`--budget 20` is the PLAN's suggestion); fix whatever the real service does differently. |
| Free volume and names | Adzuna alone gives jobs, not people. | Add a permitted contact list, or switch on Hunter / Apollo / TheirStack with a `budget` once a client's fee covers it. |
| Niche coverage | Search words live in the playbook. | One playbook copy per niche for now. Deriving search words from `roles` is listed as worth automating. |
| Non-hiring reports | The engine supports other signals; the delivery files assume jobs. | Making the column headers, file names, notes and AI prompt follow the playbook's primary signal would be needed first (not built). |
| Data terms and privacy | No legal rules built in; do-not-lists exist. | Read each provider's resale terms and the privacy rules for you, your clients and the people listed before selling (PLAN.md section 6; not legal advice). |
| Ledger safety | `data/*.db` holds each client's history. | Back it up. Losing it means re-delivering old leads. |
| Not built | Auto-emailing the report (on purpose: you review first), a client portal, billing, CRM sync. | Future work, if needed. |
| LinkedIn / Indeed scraping | Adapters exist (`linkedin_jobs`, Apify presets) but are marked "use at own risk" and are in no default playbook. | Keep them out of anything you sell unless the terms allow it. |

The part-by-part version of this table is [section 3](#3-the-stack-current-state-and-future-changes) (all changes in priority order: [3.18](#318-priority-change-list)). The operations view is [4.15](#415-operations-today-and-what-will-need-to-change), the business view [5.19](#519-current-stack-vs-business-needs-what-exists-today-and-what-each-will-need-later) and the roadmap [6.9](#69-roadmap).

---

## 2. Technical architecture

This section explains how the `leadgen` engine is built. It has two readers:

* **The owner.** Each part opens with a plain-English "what this means for you". You do not need to read the code to follow it.
* **A future developer or AI assistant.** Everything below was checked against the code on branch `claude/modest-hopper-lue9ct` (last commit `03ec275`). File paths, settings, defaults and numbers are copied from the source, not from memory. Where the code and a comment or doc disagree, this section says so.

Words such as playbook, client file, adapter, signal, lead, paid lookup, ledger and dry run are defined in [Appendix C](#appendix-c-glossary).

> **Sandbox caveat, repeated where it matters.** Outbound network access was blocked in the environment where this engine was built and reviewed. **No provider (Adzuna, TheirStack, Apollo, Apify, Hunter, MillionVerifier, ZeroBounce, NeverBounce, Anthropic, OpenAI, Google Sheets, Slack, Instantly, Smartlead) has ever been called live.** Every adapter was written from the provider's documentation and tested against canned responses in the documented shape. The first real call to each one is still to come (see 2.2 and 2.20).

### 2.1 The big picture in plain English

The engine is a command-line program called `leadgen`. You type a command such as `leadgen deliver --client acme`, and it:

1. **Finds companies that are hiring** from job sources (Adzuna, company job boards, your own CSV files, or paid sources if you switch them on).
2. **Keeps only fresh, relevant jobs** (by default for a client: posted in the last 7 days, matching the client's roles, not re-posted, with a known date).
3. **Keeps only companies that fit the client** (location, size, industry, do-not-list).
4. **Removes anything this client already received** (companies, jobs, people), *before* spending money on lookups.
5. **Finds the person most likely to own the hire** and their email, cheapest method first.
6. **Checks the email** and labels it honestly: `verified`, `risky`, `guessed-unverified` or `not found`.
7. **Scores every lead 0-100** and marks it `hot`, `normal` or `skip`.
8. **Picks the best leads** up to the client's weekly number and writes a CSV, an Excel file and an HTML page.
9. **Records what was delivered** so it is never delivered again, and prints a quality-check (QA) summary.

It never emails anyone. That is enforced in code by the **mode** setting (see 2.7). The old outreach features still exist in `mode: outbound`, switched off by default. The owner uses `playbooks/my-agency.yaml` (outbound mode) only to find clients for his own agency.

The whole engine is about 25,900 lines of Python in `leadgen/`, plus 2,073 automated tests.

### 2.2 The current stack, and what each part will need in the future

"Stack" means the technologies the engine is built on. This table is the technical summary. [Section 3](#3-the-stack-current-state-and-future-changes) treats every part in full (what is used today, cost, limits, the changes needed at each growth stage), and [3.18](#318-priority-change-list) puts all the changes in priority order. Nothing in the right-hand column is urgent for a first client unless it says so.

| Layer | What is used today | How far it is proven | Changes needed in the future (full detail in section 3) |
|---|---|---|---|
| **Language / runtime** | Python. `pyproject.toml` says `requires-python = ">=3.9"`. | All 2,073 tests pass on 3.11.15 (about 23-29 seconds). Section 6 also ran them on 3.9.23. | Python 3.9 reached end of security support in October 2025 (general knowledge, not a repo fact). Raise the minimum to 3.10+ once CI tests it, and always run on the version you test on. See [3.1](#31-language-and-runtime). |
| **Packaging** | `setuptools` via `pyproject.toml`: package `leadgen` version `0.1.0`, console command `leadgen` (`leadgen.cli:main`), installed editable (`pip install -e .`). `requirements.txt` repeats the runtime dependencies and adds `pytest`. | Works (the `leadgen` command was used throughout). | Before running for paying clients: pin exact versions (a lock file), so an automatic upgrade cannot break a Monday delivery. Split test-only packages from runtime ones. See [3.1](#31-language-and-runtime). |
| **Core libraries** | `requests>=2.28` (HTTP), `PyYAML>=6.0` (settings), `openpyxl>=3.1` (Excel). Installed in the sandbox: requests 2.33.1, PyYAML 6.0.1, openpyxl 3.1.5. | Tested. | Nothing now. Add upper version bounds with the lock file. See [3.2](#32-core-libraries). |
| **Optional library** | `gspread>=5` (extra `leadgen[sheets]`) for Google Sheets. | **Not installed in the sandbox.** Sheets code is tested only with fakes. | When a client wants a shared Sheet: `pip install -e ".[sheets]"`, create a Google service account, then test one real push. See [3.10](#310-output-and-delivery-formats). |
| **Command line** | Standard-library `argparse`, all in `leadgen/cli.py` (2,168 lines). `main(argv)` returns an exit code. | Tested through `tests/test_cli.py`, `tests/test_cli_delivery.py` and the end-to-end tests. | None required. If many commands are added, split `cli.py` into one module per command. The first operational need is a "deliver every client" wrapper. See [3.3](#33-cli-command-line-interface). |
| **Configuration** | YAML playbooks, YAML client files, and a `.env` file (the 28 variables are listed in `.env.example`). | Tested (`tests/test_e2e_delivery.py` checks that `.env.example` stays complete). | Build source search words from each client's `roles`; keep secrets in a host's secret store if the engine is ever hosted. See [3.4](#34-configuration). |
| **Storage** | One SQLite file (Python's built-in `sqlite3`). Default `data/leadgen.db` (`storage.path`); the demo uses `data/demo-delivery.db`. Tables are created with `CREATE TABLE IF NOT EXISTS`. There is no schema-version table or migration tool. | Tested. The ledger was re-checked with real (offline) deliveries. | **Now:** back up `data/*.db`: it holds every client's delivery history, and losing it means re-delivering old leads. **Before changing a table:** add a simple migration mechanism. **Only if** you build a client portal or run several machines: move to a server database such as Postgres. **For privacy:** add a purge command (nothing deletes old personal data today, see [2.17](#217-security-and-privacy-measures)). See [3.5](#35-storage-sqlite-store-and-ledger). |
| **HTTP layer** | Own `HttpClient` on a `requests.Session`: retries, backoff, secret redaction. Every adapter's traffic passes through a metering wrapper that enforces the paid-lookup budget. Synchronous: one request at a time. | Tested with `FakeHttp`. Never used against a live host. | First live week: watch for provider rate limits and time-outs. Only if runs become slow at high volume: add limited parallel requests. See [3.2](#32-core-libraries). |
| **Job sources** | Free: Adzuna (free key), Greenhouse / Lever / Ashby job boards (no key), CSV / JSON files (offline). Paid: TheirStack, Apollo, Apify (and `linkedin_jobs` via Apify, use at own risk). In `playbooks/recruitment-delivery.yaml` only Adzuna is switched on; the job boards, CSV and TheirStack entries are there but `enabled: false`. | Never called live. | Per source, before selling data from it: `leadgen doctor`, then a small real delivery with `--budget 20`, then fix any differences. Read each provider's terms on re-sharing its data. Later: make each client's `roles` drive the source search words. See [3.6](#36-job-signal-sources). |
| **Contact finders** | Free: `pattern` (guesses `first.last@...`), `csv` (your own list). Paid: Hunter, Apollo. Recruitment playbook: only `pattern` is on. | Never called live. | Switch on Hunter or Apollo when a client's price covers it, with a `budget` in the client file. See [3.7](#37-contact-finders). |
| **Email checkers** | Free: `basic` (syntax + throw-away-domain check; it can only say `invalid` or `unknown`, never `valid`). Paid: MillionVerifier, ZeroBounce, NeverBounce, Hunter. | Never called live. | With the free checker nothing is ever labelled `verified` unless the data provider itself said "valid". To sell verified emails, turn on one paid checker and set its price in `usage.cost_per_call`. See [3.8](#38-email-verification). |
| **AI models** | Raw HTTP clients (no vendor SDK) for the Anthropic Messages API (default model `claude-opus-5`) and for OpenAI Chat Completions or any OpenAI-compatible endpoint (default `gpt-5-mini`). In delivery mode AI is only used for the optional "Suggested opening line" column, which is off by default and capped at USD 0.50 per run by default. | Never called live. | Keep `LLM_PRICES` in `leadgen/usage.py` in line with provider prices (they change). OpenAI models are not in that table: set `usage.llm_price_per_mtok`, or the high fallback price is used. Try AI opening lines live with a tiny cap before offering them (see the risk in [2.20](#220-known-gaps-inconsistencies-and-things-never-verified-live)). The raw-HTTP design is deliberate: it keeps every AI call inside the budget meter and the redaction; a vendor SDK would bypass both unless routed through the meter (the SDK question is also discussed in [6.7](#67-known-limitations)). See [3.9](#39-ai-large-language-models). |
| **Output files** | CSV (standard library), Excel (`openpyxl`), HTML (hand-built, inline CSS), Google Sheets (`gspread`). | CSV / Excel / HTML re-checked with real demo deliveries; Sheets with fakes only. | PDF is not built (open the HTML page and print to PDF). Emailing the report is not built, on purpose: you review before sending. See [3.10](#310-output-and-delivery-formats). |
| **Alerts** | Console (default), Slack incoming webhook, generic webhook. | Slack and webhook never called live. | Optional later: send an alert when the delivery QA summary has warnings (today the delivery layer sends none). See [3.14](#314-monitoring-and-qa). |
| **Web server** | Standard-library `http.server`, single-threaded, outbound mode only (`leadgen serve`). | Tested locally in `tests/test_server.py`. | Only if outbound mode is used for real: put it behind HTTPS and require a token (today a missing token on a public address only logs a warning). See [3.11](#311-outbound-tools-kept-for-later). |
| **Scheduling** | None built in. The README shows `cron` (Linux / macOS) and Windows Task Scheduler lines. | Not applicable. | A machine that is on at delivery time every week. If logs are written into the repository folder (the README uses `logs/`), add `logs/` to `.gitignore`. See [3.12](#312-scheduling-and-automation). |
| **Tests** | `pytest`, 2,073 tests, fake HTTP and fake AI, a fixed test date of 2026-09-24. | All pass. | There is no CI configuration (no `.github/` folder): add a job that runs `pytest` on every push. A local `.ruff_cache/` folder suggests the linter `ruff` was used, but it is not declared anywhere; add it to the dev dependencies if you want it enforced. See [3.15](#315-testing-and-ci). |
| **Secrets** | `.env` file (git-ignored) or shell variables. | Tested. | If the engine is ever hosted: use the host's secret store and rotate keys. See [3.4](#34-configuration) and [3.16](#316-security-and-compliance). |

### 2.3 Repository layout

The repository-level files and folders (docs, playbooks, client files, sample data, tests, run-time folders, and whether git tracks each one) are in [Appendix A](#appendix-a-file-map-quick-reference). This part lists every module of the engine package with a one-line purpose.

**The `leadgen/` package, module by module**

```
leadgen/
├── __init__.py        Package marker; __version__ = "0.1.0"
├── __main__.py        `python -m leadgen` -> cli.main()
├── cli.py             Every `leadgen <command>`; main(argv) -> exit code; make_http() (the one HTTP
│                      factory tests replace); .env loading; LEADGEN_PLUGINS loading; outbound_gate
├── modes.py           DELIVERY / OUTBOUND, mode_of, is_outbound, require_outbound, OutboundOnlyError
├── playbook.py        Playbook dataclass, DEFAULTS, ${ENV} expansion, validate(), load_playbook / from_dict
├── context.py         Context (everything a run needs) + Adapter base class (secret(), metered http)
├── registry.py        type name -> "module:Class" per kind, lazy import, PAID set, risk_note, register()
├── usage.py           UsageMeter (calls, paid lookups, tokens, cost), MeteredHttp, BudgetExceeded, LLM_PRICES
├── http.py            HttpClient (retry/backoff), HttpError, redact(), safe_url()
├── models.py          Signal, Contact, Company, Message, ScoreBreakdown, Lead, Reply + vocabularies
├── utils.py           Normalisers (text, company name, domain, company_key), parse_date, to_int,
│                      is_valid_email, is_personal_email, get_path, word_count, truncate, chunks
├── store.py           SQLite Store: runs, signal history, verification cache, leads, suppression,
│                      replies, follow-ups, funnel stats
├── pipeline.py        Pipeline(ctx, out_dir, limit, hooks).run() -> RunResult; PipelineHooks; merge_companies
├── signals.py         Stage 2: process_signals, signal_stats, keyword_match (the one keyword matcher)
├── filters.py         Stage 3: check_icp / apply_icp, fit_report, excluded_domain, location knowledge
├── contacts.py        Stage 5: title ranking, generic-mailbox detection, select_contacts, ContactWaterfall
├── scoring.py         Stage 7: score() -> ScoreBreakdown, tier_for()
├── doctor.py          `leadgen doctor`: one free check per API key (run_doctor, CHECKS)
├── replies.py         (outbound) clean, classify (rules | ai | auto) and act on replies
├── server.py          (outbound) webhook receiver for reply events (make_server)
├── report.py          Demo one-pager for prospects (write_demo), funnel_report, runs_report
├── delivery/          The client-delivery layer (the product)
│   ├── __init__.py    Package overview
│   ├── client.py      Client files: parse/validate, list_clients, new_client_file, client_playbook
│   ├── ledger.py      Ledger (delivery history + per-client do-not-list), LedgerHooks, item keys
│   ├── rows.py        The client row: 18 columns (+ optional opening line), email_label, DeliveryPackage
│   ├── formats.py     write_csv / write_xlsx / write_html / push_google_sheet / write_all
│   ├── opening.py     "Suggested opening line": template_line, add_opening_lines (AI with a cost cap)
│   ├── qa.py          QAReport, build_qa, reason grouping
│   └── run.py         deliver(): the orchestration; select_leads; output folders
├── sources/           Where companies + signals come from
│   ├── __init__.py
│   ├── base.py        Source base class (label, limit, fetch())
│   ├── csv_source.py  `csv` and `json` sources (offline; auto-detected columns)
│   ├── mapping.py     Shared record -> Company mapping (column detection, dates, money, domains)
│   ├── adzuna.py      `adzuna` job search API
│   ├── theirstack.py  `theirstack` job search API (paid; also returns hiring-team contacts)
│   ├── apollo.py      `apollo` company search (paid; funding / headcount / optional job signals)
│   ├── apify.py       `apify` (any Apify actor; presets google_maps, linkedin_jobs, indeed_jobs) and
│   │                  `linkedin_jobs` (use at own risk)
│   └── ats.py         `greenhouse`, `lever`, `ashby` public job boards (no key)
├── enrich/            Contact finders (who to talk to + their email)
│   ├── __init__.py
│   ├── base.py        ContactFinder base (find(), optional complete())
│   ├── csv_finder.py  `csv` people from your own file (offline)
│   ├── apollo.py      `apollo` people search + email reveal (paid)
│   ├── hunter.py      `hunter` domain search + email finder (paid)
│   └── pattern.py     `pattern` email guesses from name + domain (offline)
├── verify/            Email checkers
│   ├── __init__.py
│   ├── base.py        Verifier base, VerificationResult, VerifierError
│   ├── basic.py       `basic` offline checker + ApiVerifier base for paid checkers
│   ├── millionverifier.py, zerobounce.py, neverbounce.py, hunter.py   paid checkers
├── llm/               AI model clients
│   ├── __init__.py    build_llm(ctx) from the playbook's writer section
│   ├── base.py        LLMClient base, LLMError, LLMConfigError, LLMTruncatedError, parse_json_block
│   ├── anthropic.py   `anthropic` Messages API client (default model claude-opus-5)
│   └── openai.py      `openai` and `openai_compatible` Chat Completions client (default gpt-5-mini)
├── writer/            (outbound) email sequence writers
│   ├── __init__.py    build_writer(ctx)
│   ├── base.py        Writer base, WriterOutput
│   ├── template.py    `template` writer (offline) + shared text helpers
│   ├── ai.py          `ai` writer (LLM, guardrails, one retry, template fallback)
│   ├── prompts.py     System / user / feedback prompts
│   └── guardrails.py  Deterministic checks on written copy
├── outbound/          Exporters (review files) and senders (hand-over to sending tools)
│   ├── __init__.py
│   ├── base.py        Exporter base, ExportResult, `scope` ("all" | "outbound")
│   ├── csv_export.py  `csv` (opportunities.csv) and `json` review exporters; guard_cell (formula guard)
│   ├── instantly.py   `instantly_csv` upload file and `instantly` API sender (outbound)
│   ├── smartlead.py   `smartlead_csv` upload file and `smartlead` API sender (outbound)
│   ├── gsheets.py     `gsheets` review sheet in Google Sheets
│   └── webhook.py     `webhook` exporter: POST leads as JSON (outbound)
└── notify/            Alerts
    ├── __init__.py    notify(ctx, event, title, text, data) dispatcher (never raises)
    ├── base.py        Notifier base
    ├── console.py     `console` (default)
    ├── slack.py       `slack` incoming webhook
    └── webhook.py     `webhook` generic JSON POST
```

### 2.4 Data flow (ASCII diagram)

```
  playbooks/<name>.yaml                           clients/<name>.yaml
          |                                                |
  playbook.load_playbook()                       delivery.client.load_client()
  DEFAULTS + ${ENV} + validate()                 validate every key ("did you mean")
          |                                                |
          |                                   client_playbook(): base playbook + client
          |                                   targeting; mode forced to delivery;
          |                                   hand-over exporters removed
          v                                                v
  .env / shell env ---> Context(playbook, http, store, env, today, dry_run, usage = UsageMeter)
                                         |
+----------------------------------------v--------------------------------------------------+
| Pipeline.run()                                                                            |
|                                                                                           |
| 1  SOURCE    each enabled source .fetch()                                                 |
|              adapter.http = MeteredHttp(budget) -> HttpClient(retry, redact) -> provider  |
|              merge_companies(): same domain, else same normalised name                    |
| 2  SIGNALS   process_signals()  <------> store.signal_history (first_seen, reposted)      |
| 3  ICP       apply_icp()        <------> store.suppression (global do-not-list)           |
| 3b HOOKS     hooks.filter_company()  <-- LedgerHooks <--> deliveries, client_suppression  |
| 4  PRESCORE  score(company, no contact); --limit N; enrichment.max_companies              |
| 5  ENRICH    ContactWaterfall: finder 1 -> finder 2 -> pattern (stops when reachable)     |
|              late domain check + hooks.filter_enriched()                                  |
| 6  VERIFY    verify_contact(): up to 3 addresses <--> store.verifications (30-day cache)  |
|              hooks.filter_contact() before and after                                      |
| 7  SCORE     score() + tier_for() -> Lead(stage, notes)                                   |
| 8  WRITE     [outbound only] build_writer() -> template | ai (LLM) sequences              |
|    SAVE      store.save_lead() -> leads, lead_runs, events                                |
| 9  EXPORT    [outbound only] hand-over exporters -> store.mark_exported()                 |
|              review exporters (csv / json / gsheets) -> <out>/<run id>/                   |
|    FINISH    rejected.csv + summary.json, store.finish_run(), notify("run_summary")       |
+----------------------------------------+--------------------------------------------------+
                                         | RunResult(leads, counts, rejected, errors,
                                         |           warnings, usage lines)
              leadgen run  <-------------+-------------> leadgen deliver --client NAME
              (stops here)                                   |
                                                             v
                     select_leads()  -> build_row() -> add_opening_lines() -> write_all()
                     (tiers, live job,     (email         (template, or AI     (CSV / XLSX /
                      1 per company +       labels)        under a cost cap)    HTML)
                      person, limit)
                             -> push_google_sheet() (optional, never in a dry run)
                             -> Ledger.record()     (never in a dry run)
                             -> build_qa() -> _internal/qa.txt, qa.json, not_delivered.csv

  Outbound mode only (reply side):
  replies CSV (`leadgen replies --file`) or webhook POST (`leadgen serve`)
      -> parse_webhook_payload / load_replies_csv -> handle_reply() -> classify (rules | ai | auto)
      -> link to a lead -> stage change / suppression / follow-up -> notify() -> store.save_reply()
```

### 2.5 Data model (`leadgen/models.py`)

**What this means for you:** these are the "record types" the engine passes from stage to stage. You will see their fields as columns in the review files.

All entities are plain Python dataclasses, so adapters stay simple.

| Entity | What it is | Key fields | Identity |
|---|---|---|---|
| `Signal` | A reason to buy now (a job post, funding round, ...) | `type`, `title`, `source`, `posted_at`, `url`, `location`, `description`, `external_id`, `data`; set by the signals stage: `reposted`, `first_seen` | `fingerprint` = `"<type>:<normalised title>"`. `age_days(today)` uses `posted_at`, else `first_seen`, else unknown. |
| `Contact` | A person at a company | names, `title`, `email` (lower-cased), `email_status`, `email_candidates` (guesses to verify, in order), `linkedin_url`, `phone`, `seniority`, `department`, `location`, `source`, `confidence`, `data` | `key` = email, else LinkedIn URL, else normalised full name. An invalid `email_status` becomes `unknown`. A full name like "Dr. X Y" is split into first/last. |
| `Company` | An account; signals and contacts accumulate on it | `name`, `domain` (normalised from domain or website), `website`, `linkedin_url`, `location`, `country`, `industry`, `employees`, `description`, `keywords`, `signals`, `contacts`, `sources`, `data` | `key` = `utils.company_key` = the domain, else `"name:<normalised name>"`. `merge()` fills blanks from another record and de-duplicates signals and contacts. |
| `Message` | One step of an outbound email sequence | `step`, `day`, `subject`, `body` | - |
| `ScoreBreakdown` | The four score parts plus reasons | `intent`, `fit`, `reachability`, `extra`, `reasons` | `total` = the sum, clamped to 0..100 and rounded to a whole number. |
| `Lead` | A company + the chosen contact, scored | `company`, `contact`, `score`, `tier`, `breakdown`, `messages`, `personalization`, `hypothesis`, `writer`, `stage`, `playbook`, `run_id`, `notes` | `id` = first 16 hex characters of SHA-1 of `"<playbook>\|<company key>\|<contact key>"`. Client deliveries run under playbook name `client-<name>`, so lead ids are per client. `top_signal` = first signal after sorting. |
| `Reply` | (outbound) an inbound reply and its classification | `from_email`, `body`, `subject`, `received_at`, `lead_id`, `category`, `confidence`, `summary`, `referral_name`, `referral_email`, `follow_up_date`, `suggested_reply`, `action`, `classifier`, `data` | - |

**Vocabularies** (fixed word lists):

| Class | Values |
|---|---|
| `SignalType` | `job_posting`, `funding`, `leadership_change`, `expansion`, `headcount_growth`, `tech_adoption`, `news`, `review`, `ad_activity`, `website_change`, `event`, `custom` (any other string is also allowed) |
| `EmailStatus` | `unknown` (not checked / checker could not decide), `valid`, `risky` (catch-all domain), `invalid` |
| `Tier` | `hot`, `normal`, `skip` |
| `Stage` (funnel order) | `sourced` -> `qualified` -> `enriched` -> `verified` -> `ready` -> `exported` -> `replied` -> `positive` -> `booked` -> `won`, plus `lost` |
| `ReplyCategory` | `positive`, `referral`, `timing`, `question`, `negative`, `unsubscribe`, `ooo`, `bounce`, `other` |

Note: `EmailStatus` is the engine's internal status. The client file never shows it directly: it shows one of four client labels (see 2.9, `rows.py`).

### 2.6 Configuration: playbooks (`leadgen/playbook.py`)

**What this means for you:** one YAML file holds every setting. Anything you leave out gets a sensible default. You can put `${ADZUNA_APP_KEY}` in a file instead of the key itself, and the key is read from `.env`.

**Loading** (`load_playbook(path)` -> `from_dict(data)`):

1. Parse YAML (`yaml.safe_load`).
2. Fix YAML's quirk that reads an unquoted `on:` key as `True` (and `off:` as `False`), so `notify.on` works.
3. Expand `${VAR}` and `${VAR:-default}` in every string, recursively. An unset or empty variable becomes the default, or `""` when no default is given.
4. Deep-merge the file over `DEFAULTS` (dictionaries merge key by key; lists replace the default list).
5. A single mapping where a list is expected (`exporters: {type: csv}`) becomes a one-item list, for `enrichment.finders`, `outbound.exporters` and `notify.channels`.
6. Validate. All problems are reported at once as `PlaybookError`.

**Environment:** the CLI loads `.env` (or `--env-file PATH`) first. Variables already set in the shell always win. A missing default `.env` is skipped; a missing explicit `--env-file` is an error.

**`DEFAULTS` sections** (every key exists after loading):

| Section | Purpose | Notable defaults |
|---|---|---|
| `name` | Playbook name (required; letters, digits, `-`, `_`, `.`) | - |
| `description` | Free text | `""` |
| `mode` | `delivery` or `outbound` | `delivery` |
| `offer` | Sender identity and pitch (outbound copy) | `cta: "Worth a quick chat?"`, `language: English` |
| `icp` | Ideal customer: `locations`, `exclude_locations`, `employees {min, max}`, `industries`, `exclude_industries`, `keywords`, `exclude_keywords`, `exclude_domains`, `exclude_company_patterns` (regexes), `require_domain`, `unknown_passes` | all empty; `require_domain: false`; `unknown_passes: true` |
| `signals` | Which "why now" signals count | `types: []` (= all), `primary: [job_posting]`, `require: true`, `match_keywords: []`, `exclude_keywords: []`, `max_age_days: 60`, `urgency_keywords: [urgent, immediate, immediately, asap, start now, quick start]`, `stale_after_days: 21`, `match_description: true`, `drop_reposts: false`, `allow_undated: true` |
| `buyers` | Who to contact | `titles: []`, `exclude_titles: [intern, assistant, student, trainee]`, `max_contacts_per_company: 1`, `allow_generic_emails: false` |
| `sources` | List of source adapters | `[]` |
| `enrichment` | Finders + checker | `finders: []`, `verifier: {type: basic}`, `accept_statuses: [valid, risky]`, `accept_guessed_statuses: [valid]`, `max_companies: 200`, `min_prescore: 0`, `skip_if_contact_present: true` |
| `scoring` | Weights, tiers, intent split | see 2.8, stage 7 |
| `writer` | (outbound) copy writing and the AI model settings | `type: template`, `provider: ""`, `max_words: 90`, 4-step sequence (days 1, 3, 7, 12), banned phrases, `max_tokens: 4000`, `max_llm_failures: 3`, `max_leads: 500`, `tiers: [hot, normal]` |
| `outbound` | Exporters and hand-over rules | `exporters: [{type: csv}]`, `tiers: [hot, normal]`, `require_email: true`, `dedupe_days: 90`, `company_cooldown_days: 30` |
| `replies` | (outbound) reply classifier | `classifier: auto`, `timing_default_days: 30`, `ooo_default_days: 7`, `max_tokens: 0` |
| `notify` | Alert channels | `channels: [{type: console}]`, `on: [positive, referral, question, run_summary]` |
| `storage` | Database path | `path: data/leadgen.db` |
| `delivery` | Branding for client files | `brand_name: "Hiring Signal Report"`, `brand_color: "#1f4e79"`, sender name / email, website, logo URL, footer |
| `usage` | Cost control | `max_paid_lookups: 0` (no cap), `cost_per_call: {}`, `llm_price_per_mtok: {}` |

Note that the `writer` section is read in delivery mode too: its `provider` / `model` choose the AI model for optional AI opening lines.

**Validation rules** (`validate()`): `name` present and well-formed; `mode` is `delivery` or `outbound`; every section has the right shape; each `sources` / `finders` / `exporters` / `channels` entry has a `type`; `icp.employees.min/max` are whole numbers with min <= max; each `exclude_company_patterns` entry is a valid regex; `scoring.tiers` satisfy `0 <= normal <= hot <= 100`; weights are non-negative numbers; `writer.type` is `ai` or `template` (and `ai` needs provider `openai`, `anthropic` or `openai_compatible`); `writer.sequence` is non-empty with whole-number days in ascending order; `writer.max_words >= 20`; `usage.max_paid_lookups` is a whole number >= 0; `delivery.brand_color` is a hex colour like `#1f4e79`; `replies.classifier` is `auto`, `rules` or `ai`.

Relative file paths inside a playbook (for example a CSV source's `path`) are looked up in the current folder first, then next to the playbook.

### 2.7 Modes and the outbound guard (`leadgen/modes.py`)

**What this means for you:** by default the engine cannot send, write emails for, or process replies from anyone. You would have to write `mode: outbound` in a playbook on purpose.

| | `delivery` (default) | `outbound` |
|---|---|---|
| The business | Sell lead files | Run outreach yourself |
| WRITE stage | Skipped: `build_writer` is never called | Runs |
| Hand-over exporters (`instantly`, `instantly_csv`, `smartlead`, `smartlead_csv`, `webhook`) | Never built; a warning names each one | Run, and mark leads as EXPORTED |
| `leadgen replies`, `serve`, `followups` | Refused, exit code 2 | Work |

The guard is layered, so one missed check cannot cause a send:

| Where | What it does |
|---|---|
| `cli.outbound_gate` | Used by `replies`, `serve` and `followups`. Refuses (exit 2) **before** opening the database, the network or any file. Without `-p`, the default mode (delivery) applies, so the command is refused. Verified in this review: `leadgen replies -p playbooks/demo-delivery.yaml ...` printed the refusal and exited 2. |
| `replies.handle_reply` | Calls `require_outbound(ctx, "Reply handling")` first; raises `OutboundOnlyError`. |
| `server.make_server` | Calls `require_outbound(ctx, "The reply webhook server")`. |
| `Pipeline` | `self.outbound = is_outbound(ctx)`. The writer is built only when outbound. Exporter types whose class has `scope = "outbound"` are skipped by a class check *before* they are built, and again by an instance check after building. |
| `delivery.client.client_playbook` | Always sets `mode: delivery` and removes hand-over exporters, whatever the base playbook says. A client file's `overrides` may not set `mode` or `name`. |
| `delivery.run.deliver` | Sets `pb.mode = DELIVERY` again before running. |
| `doctor` | Skips hand-over exporters in delivery mode ("not used in delivery mode"). |
| `leadgen validate` | In delivery mode, outbound-only settings (AI writer, hand-over exporters) are warnings, not failures. |

### 2.8 The pipeline, stage by stage (`leadgen/pipeline.py`)

**What this means for you:** this is the factory line. Every stage is defensive: one broken source, a missing key or one odd company is logged as an error, and the run carries on with what it has. Running out of paid budget becomes a warning, not a crash.

`Pipeline(ctx, out_dir=None, limit=None, hooks=None).run() -> RunResult`. `out_dir` defaults to `output/<playbook name>`; each run writes to `<out_dir>/<run id>/`. The run id looks like `20260925-064525-e79dae` (UTC time + 6 random hex characters).

**Stage 1 - SOURCE (`collect`)**

* Each source in `sources` runs unless `enabled: false`. A source is built with `registry.create("source", cfg, ctx)`. In a dry run, sources that use the network are skipped.
* Errors caught per source: missing credential, HTTP error, unknown type, file error, bad value. They go to `RunResult.errors` (secrets redacted).
* `BudgetExceeded` stops that source and becomes a warning. Apollo and TheirStack sources keep what they had already paid for when the budget runs out part-way (they set `budget_stop`, which the pipeline reports).
* `merge_companies`: records for the same company are merged: first by domain, then by normalised name (case, accents, punctuation and legal suffixes ignored). **Same name + different domains = different companies.**

**Stage 2 - SIGNALS (`signals.process_signals`)**

Per company, in this order:

1. Drop exact duplicate signals (same fingerprint, external id, URL, location and posting date).
2. Drop signals whose type is not in `signals.types` (when set).
3. Drop signals whose title matches `signals.exclude_keywords` (e.g. "intern").
4. For **primary** types only: the title must match `signals.match_keywords`; the description is a fallback unless `match_description: false`.
5. Record the survivors in the store (`store.observe_signals`), which sets:
   * `first_seen` = the earliest day this company + title was ever seen (or the posting date, if earlier than today, on the first sighting);
   * `reposted` = true when the same title was seen before under a different job id that is no longer live, or when the posting date is 7 or more days after the first sighting. Several postings with the same title live in the same batch (one role in several cities) are **not** treated as re-posts of each other, unless the source itself said so.
6. With `allow_undated: false`, drop primary signals without a posting date.
7. Drop signals older than `max_age_days` (an undated signal ages from its first sighting; `null` switches the age limit off).
8. With `drop_reposts: true`, drop primary signals flagged as re-posted.
9. Sort: primary first, then freshest, undated last.

With `signals.require: true` (default), a company left with no signal is rejected, with a readable reason such as `no signal matching [accountant, ...] in last 7 days (3 signals found: 1 excluded by keyword (intern); 2 older than 7 days)`.

**Keyword matching** (`signals.keyword_match`, used everywhere: ICP, titles, urgency): case- and accent-insensitive, whole words or whole phrases only (`cto` never matches "director"), tolerant of simple plurals (`accountant` matches "Accountants"), `e-commerce` matches `ecommerce`, and short words that double as acronyms (`IT`, `US`, `IN`, ...) only match when written in capitals in the text.

For client deliveries the signal settings come from the client file: `freshness_days` (default 7) -> `max_age_days`, `allow_undated` (default false), `drop_reposts` (default true). `deliver()` also makes sure `job_posting` is a primary type, because these rules apply to primary types only.

**Stage 3 - ICP FILTER (`filters.apply_icp`)**

Checks run in this order; the first failure is the rejection reason in `rejected.csv`:

1. Global suppression list: the company's domain (exact domain), then its name.
2. `icp.exclude_domains` (a domain also excludes its subdomains).
3. `icp.exclude_company_patterns` (regex on the name, case-insensitive).
4. `icp.require_domain`.
5. `icp.exclude_keywords` in name, industry, keywords or description.
6. `icp.keywords` (when set, one must appear in name, industry, keywords, description or a signal title).
7. Location, size, industry fit (`fit_report`). A mismatch rejects. Unknown data passes only while `unknown_passes: true`.

Location matching knows common aliases (US / USA / United States; UK / GB / Great Britain, with England, Scotland, Wales, Northern Ireland counting as UK), US state names and two-letter codes, some regions of other countries, and a few groupings (Europe, North America, DACH, Nordics, Benelux, ANZ). Signal locations count when the company's own location is unknown. "Remote" / "Anywhere" count as unknown.

**Stage 3b - HOOKS (`PipelineHooks`)**

A caller can pass hooks; the delivery layer passes `ledger.LedgerHooks` (see 2.9). There are three:

| Hook | When it runs | Return a reason to... |
|---|---|---|
| `filter_company(company)` | Right after the ICP filter, **before any paid lookup**. It may also remove signals (e.g. jobs already delivered). | Drop the company (stage `delivery` in `rejected.csv`). |
| `filter_enriched(company)` | After enrichment, before any email check (more is known now, e.g. the email domain of a company that arrived without a website). | Drop the company. |
| `filter_contact(company, contact)` | Before a contact is verified, and again once its email is known. | Skip that person. |

`counts["hook_rejected"]` counts companies removed by hooks.

**Stage 4 - PRE-SCORE**

Every company is scored *without a contact* and sorted best first, so enrichment money is spent on the best accounts. `--limit N` keeps only the top N companies (the rest are dropped from the run). Only the first `enrichment.max_companies` companies (default 200; a client delivery sets `max(leads_per_week x 2, 10)` unless the base playbook sets a lower number) with a pre-score of at least `enrichment.min_prescore` go through the finder waterfall. Companies past that line still become leads with whatever contacts their source supplied, and those contacts are still verified.

**Stage 5 - ENRICH (`contacts.ContactWaterfall`)**

* Finders from `enrichment.finders` run in order (for example Apollo -> Hunter -> pattern). Finders that use the network are skipped in a dry run.
* Nothing runs when the company's domain is in `icp.exclude_domains`, or when `skip_if_contact_present` is on and the company already has a target-title person with a usable (personal, not invalid) email.
* Each finder's `find(company)` adds people (a known person only gets blanks filled, or an unusable email replaced). Then `complete(company, contact)` is asked to fill a missing email for target-title people, best-ranked first. The `pattern` finder fills `email_candidates` this way.
* The waterfall **stops** as soon as a target-title person (anyone, if `buyers.titles` is empty) has a usable email or email guesses to check. So paid finders are not called when a free one already succeeded.
* A missing key or an HTTP 401 / 402 / 403 disables that finder for the rest of the run. `BudgetExceeded` quietly disables a paid finder; free finders keep going.
* After enrichment: a company that arrived without a website is dropped if its people's email domain is on the global suppression list or in `icp.exclude_domains`, then `hooks.filter_enriched` runs.

**Contact selection** (`select_contacts`): drops excluded titles, people at excluded domains, and generic mailboxes such as `info@`, `hr@`, `jobs@` (unless `buyers.allow_generic_emails`; a *named* person with a generic address is kept without the address). Ranking: buyer titles by priority, then other titles; ties prefer having an email, then valid > risky > unknown > invalid, then higher provider confidence. Title matching understands common abbreviations (VP, CFO, HR, TA, MD, ...) and ignores "former", "deputy", "assistant" and similar qualifiers.

The pipeline takes up to `max_contacts_per_company x 3` candidates (client deliveries: 1 x 3), skips suppressed people and hook-rejected people, verifies them, and stops once it has `max_contacts_per_company` people with an acceptable email.

**Stage 6 - VERIFY (`Pipeline.verify_contact`)**

* Runs only for a contact with no email or an email of status `unknown`.
* Candidate addresses: the provider's address first, then the guesses; **at most 3** are checked (`MAX_CANDIDATES_TO_VERIFY = 3`).
* The store's verification cache is checked first (results younger than 30 days). Every result other than `unknown` is cached, shared by all playbooks and clients.
* If the paid-lookup budget is used up and the checker is a paid one, it is swapped for the free `basic` checker (warning).
* `valid` wins at once. Otherwise the best accepted status is kept. A provider-supplied address with an accepted status stops the search.
* **The guessed-email rule, part 1:** if the address that wins is not the one the provider supplied, the contact is marked `data["email_guessed"] = True`.
* `hooks.filter_contact` runs again once the email is known.

**Stage 7 - SCORE (`leadgen/scoring.py`)**

**What this means for you:** the score says how urgent and how reachable a lead is. Hot leads should be called first.

`score(company, contact, ctx)` returns four parts. Each is capped by its weight.

| Part | Formula |
|---|---|
| **intent** (max `weights.intent`) | `earned / sum(sub-points) x weights.intent`, where earned = sum of each sub-point x its credit. 0 when there is no primary signal. |
| - `fresh` credit | Age of the freshest primary signal against the bands `scoring.freshness_days`. Within band *i* of *n*: `(n - i) / n` (so full, 3/4, 1/2, 1/4 with four bands). Older than the last band: 0. Age unknown: 1/4. |
| - `volume` credit | `min(1, (volume - 1) / (volume_full_at - 1))`, where volume = number of primary signals. One signal earns nothing. If `volume_full_at <= 1`, any primary signal earns full points. |
| - `persistence` credit | 1 if a primary signal was re-posted or is at least `signals.stale_after_days` (21) old, else 0. |
| - `urgency` credit | 1 if a primary signal's title or description uses `signals.urgency_keywords`, else 0. |
| **fit** (max `weights.fit`) | Average over location, size and industry of: 1 (match, or not configured), 0 (mismatch), `unknown_credit` (default 0.5, data unknown); times `weights.fit`. |
| **reachability** (max `weights.reachability`) | `(0.5 x person + 0.5 x email) x weights.reachability`. Person: no contact 0; excluded title 0; no `buyers.titles` configured 0.8; buyer #1 1.0; buyer #2-#3 0.8; lower buyer 0.6; not a target title (or title unknown) 0.3. Email: none or invalid 0; `valid` 1.0; `risky` 0.5; `unknown` 0.3. |
| **extra** (max `weights.extra`) | `extra_per_signal` (5) for each distinct *secondary* signal type (funding, expansion, ...). |
| **total** | Sum, clamped to 0..100, rounded. |

`tier_for(total)`: `hot` if total >= `tiers.hot`, `normal` if >= `tiers.normal`, else `skip`.

| Setting | Engine default (`DEFAULTS`) | `recruitment-delivery.yaml` and `demo-delivery.yaml` |
|---|---|---|
| `weights` (intent / fit / reachability / extra) | 40 / 30 / 20 / 10 | 45 / 15 / 30 / 10 |
| `intent` sub-points (fresh / volume / persistence / urgency) | 15 / 10 / 10 / 5 | 20 / 10 / 5 / 5 |
| `freshness_days` bands | [3, 7, 14, 30] | [2, 4, 7, 14] |
| `tiers` (hot / normal) | 80 / 60 | 65 / 15 |
| `volume_full_at`, `extra_per_signal`, `unknown_credit` | 3, 5, 0.5 | 3, 5, 0.5 |

**Worked example**, re-computed against real output of the offline demo (`leadgen deliver --client demo-client`), delivery-playbook settings. A company with 2 matching jobs, the freshest posted 1 day ago, no re-post, no urgent wording; location, size and industry all match; the contact is buyer #1 (CFO) with a `valid` email:

* intent: fresh 20 x 1 + volume 10 x 0.5 + 0 + 0 = 25 of 40 sub-points -> 25 / 40 x 45 = 28.1
* fit: 15 (all three match)
* reachability: (0.5 x 1.0 + 0.5 x 1.0) x 30 = 30
* extra: 0
* total 73.1 -> **73, hot** (65 or more). The demo output showed exactly 73. The same company shape plus a funding signal scored 78 (+5 extra); with buyer #2 instead of #1 it scored 70.

In the pre-score (no contact yet) reachability is 0, so under the delivery settings a pre-score can reach at most 70.

**Stage 7, continued - lead stage and notes.** Each company becomes one or more `Lead`s (a lead with no contact if nobody was found). Stage is `qualified`, then `enriched` if a contact exists. It becomes `verified` only when the email is acceptable (`email_ok`) **and**, in delivery mode, confirmed (`email_confirmed`: status `valid` and not guessed). Notes explain gaps: `no decision-maker found`, `no email found`, `email risky`, `guessed email only risky (needs valid)`.

* `email_ok(contact)`: has an email whose status is in `enrichment.accept_statuses` (default `valid`, `risky`); a guessed address must also be in `accept_guessed_statuses` (default `valid` only). In delivery mode this only decides which person is picked for the file.
* `email_confirmed(contact)`: status `valid` and `rows.is_guessed(contact)` is false. This is the rule behind the `verified` label (**the guessed-email rule, part 2**).

**Stage 8 - WRITE (outbound only)**

Skipped entirely in delivery mode. In outbound mode: leads in `writer.tiers` with a contact, an acceptable email (when `outbound.require_email`) and no `block_reason`, one per email address (best score wins), up to `writer.max_leads` (500), get a sequence from `build_writer(ctx).write(lead)`. The `ai` writer is used only when `writer.type: ai`, an AI model is configured and it is not a dry run; otherwise the free `template` writer. A lead with messages becomes stage `ready`.

Then every lead is saved (`store.save_lead`); the stored stage never moves backwards and `lost` is sticky.

**Stage 9 - EXPORT**

* **Hand-over exporters** (`scope = "outbound"`: `instantly`, `instantly_csv`, `smartlead`, `smartlead_csv`, `webhook`): outbound mode only. They run first and receive only `outbound_leads(leads)`: leads in `outbound.tiers` that have messages and no `block_reason`, one per email address. What they hand over is marked EXPORTED (`store.mark_exported`), except in a dry run.
* **Review exporters** (`scope = "all"`: `csv` -> `opportunities.csv`, `json`, `gsheets`): both modes; they receive every lead. They run after hand-over, so the review sheet shows final stages.

`block_reason(lead)` (outbound), checked in this order: no deliverable email (when required) -> anyone at the company (same playbook) replied, was positive, booked, won or lost (a loss caused only by a bounce does not count) -> email or company domain suppressed -> email at an excluded domain -> this lead was already handed over -> same email handed over within `outbound.dedupe_days` (90) -> company handed over within `outbound.company_cooldown_days` (30).

**Run files and result**

* `<out_dir>/<run id>/rejected.csv`: company, domain, stage (`signals`, `icp`, `delivery`), reason. Cells are formula-injection guarded (see 2.17).
* `<out_dir>/<run id>/summary.json`: playbook, mode, counts, errors, warnings, usage rows, top 20 leads.
* `store.finish_run(run_id, counts)` and `notify("run_summary")`.
* `RunResult` fields: `run_id`, `playbook`, `out_dir`, `counts`, `leads`, `rejected`, `exports`, `errors`, `warnings`, `mode`, `usage` (the usage summary lines).
* `counts` keys: `sourced`, `with_signal`, `qualified`, `hook_rejected`, `enriched`, `verified`, `usable_email`, `hot`, `normal`, `skip`, `written`, `exported`, `outbound_eligible`, `paid_lookups`, `suppressed_contacts` (some only when non-zero).

**Dry run** (`--dry-run`, `ctx.dry_run = True`): only adapters with `offline = True` run (CSV / JSON sources, CSV / pattern finders, basic checker, template writer, file exporters, console). A network checker falls back to `basic`; AI writer, AI opening lines and AI reply classifier fall back to templates / rules. Delivery files are named `...-PREVIEW.<ext>`, nothing is written to the ledger and there is no Google Sheets push. Note for developers: a dry run **does** still write to the rest of the database (the run, the leads, signal sightings). Checked in this review: after one dry-run delivery the database held 1 run, 13 leads and 33 signal-history rows, and 0 ledger rows.

### 2.9 The delivery layer (`leadgen/delivery/`)

**What this means for you:** this is the product. One command per client makes that week's files, removes everything already sent, labels every email honestly and prints a QA summary. The binding rules are in `docs/DELIVERY.md`.

`leadgen deliver --client NAME` calls `run.deliver(client, store=..., env=..., http=..., today=..., dry_run=..., budget=..., out_dir=...)`.

**Order of work in `deliver()`:**

1. Load and validate the client file; build its playbook (`client_playbook`); force delivery mode; make `job_posting` primary.
2. Choose a free output folder (never overwrite an earlier delivery).
3. Build a `Context` with a `UsageMeter`. Paid-lookup cap: `--budget N`, else the client's `budget.max_paid_lookups` (if > 0), else the base playbook's `usage.max_paid_lookups`. 0 = no cap.
4. Run the pipeline with `LedgerHooks`, writing internal files to `<folder>/_internal/`.
5. `select_leads`; `build_row` for each; opening lines if enabled.
6. `write_all` (CSV / XLSX / HTML). A real delivery moves this client's same-day PREVIEW files into `_internal/preview/`.
7. Google Sheets push if configured (never in a dry run, never with 0 rows; a failure is a QA warning, the files are still complete).
8. `Ledger.record(...)` for every delivered row (not in a dry run).
9. `build_qa` -> `_internal/qa.txt`, `qa.json`, `not_delivered.csv`.

**`client.py` - client files and how they map onto the playbook**

Client names must match `^[a-z0-9][a-z0-9_-]*$`. Unknown keys are errors with a "did you mean" hint; all problems are listed at once (`ClientError`). `list_clients` skips files starting with `_` or `.`. `new_client_file` copies `clients/_template.yaml` and never overwrites. On case-insensitive disks the client name is taken from the real file name (it is the ledger's key).

Every client-file key, with its default, its rules and its exact effect on the playbook, is in the table in [4.5.2](#452-every-client-file-key). For example: `roles` **replaces** `signals.match_keywords` (empty keeps the base list); `exclude_roles` is **added to** `signals.exclude_keywords`; `locations`, `exclude_locations`, `industries` and `company_size` always replace the base playbook's `icp.*` values; `exclusions.companies` is applied by `LedgerHooks` (a "company" written like a domain also counts as a domain); `leads_per_week` also sets `enrichment.max_companies = max(leads_per_week x 2, 10)` unless the base playbook sets a lower number; `freshness_days` sets `signals.max_age_days` and also widens Adzuna `max_days_old` / TheirStack `max_age_days` when they look back less; `budget.max_paid_lookups` is copied to `usage.max_paid_lookups` when > 0; `overrides` deep-merges any playbook section last (never `name` or `mode`).

Always set by `client_playbook`: `mode: delivery`, `name: client-<name>`, `buyers.max_contacts_per_company: 1` (an `overrides` block can change this one), hand-over exporters removed, a description naming the base playbook.

**`ledger.py` - never deliver the same thing twice**

Three tables in the same SQLite file as the store, created on first use:

| Table | Primary key | Purpose |
|---|---|---|
| `deliveries(client, kind, key, run_id, delivered_at)` | (client, kind, key) | One row per delivered item. Recording again moves the item to the latest run and day. |
| `client_suppression(client, kind, value, reason, added_at)` | (client, kind, value) | Each client's own do-not-list (`leadgen suppress add ... --client NAME`) |
| `delivery_runs(client, run_id, delivered_at, items)` | (client, run_id) | Append-only: one row per delivery run (for the delivery count and dates) |

Item keys (the same functions build them when recording and when checking):

| Kind | Key |
|---|---|
| `company` | `company.key` (domain, else `name:<normalised name>`) |
| `job` | `<company key>\|<external id>`; else the job URL with scheme, `www.`, fragment and tracking parameters (`utm_*`, `gclid`, `gh_src`, ...) removed (parameters that identify the job are kept); else `<company key>\|<fingerprint>` |
| `contact` | Every identity: the email, the LinkedIn profile (country / mobile hosts folded to `linkedin.com`, only `/in/<name>` kept), and `<normalised name>\|<company key>` |

Because a company is not always seen with its domain (Adzuna never has one), a company delivered *with* a domain is also recorded under "by name" kinds (`company_by_name`, `job_by_name`, `contact_by_name`). A company seen later without its domain is checked against them. Same name + a different domain is never matched. These bookkeeping rows are not counted in summaries. Verified in this review: after one real demo delivery (10 rows) the ledger held company 10, company_by_name 9, contact 23, contact_by_name 9, job 17, job_by_name 16 rows; a second delivery the same day then delivered only the 2 leads that had been held back, reported "10 already delivered to this client", and brought the ledger to company 12, company_by_name 11, contact 29, contact_by_name 11, job 19, job_by_name 18.

**Window:** `redelivery_days = None` means "delivered ever". With N days, only a delivery with `delivered_at` later than `today - N days` counts.

**`LedgerHooks`** (a `PipelineHooks` subclass):

* `filter_company`: company on the client's do-not-list (stored domain / name / LinkedIn page, or the client file's `exclusions`) -> dropped. Then, if `company` is in `dedupe` and it was delivered -> "already delivered to this client (<date>)". Then, if `job` is in `dedupe`, already-delivered jobs are removed; a company left with no job posting -> "all its jobs were already delivered".
* `filter_contact`: the person's email, email domain (and its parent domains) or LinkedIn is on the do-not-list -> skipped. If the company had no domain and the person's email domain is on the list, the whole company is marked and emptied. Then, if `contact` is in `dedupe` and any identity was delivered -> skipped.
* `filter_enriched`: after enrichment, a company without a website whose people turn out to be at a do-not-list domain is dropped.
* `removed` counts `company`, `job`, `contact` and `suppressed` for the QA report.

**`run.select_leads` - what goes in the file**

1. Drop leads without a live job posting (or another primary signal) -> kind `filtered`.
2. Drop leads whose tier is not in `client.tiers` -> `filtered`.
3. Sort by: tier (hot first), then score (high first), then the freshest job, then company name.
4. One row per company and one per person (same email or LinkedIn profile) -> duplicates get kind `duplicate`.
5. Stop at `leads_per_week`. The rest get kind `over_limit` ("held back"): **not recorded**, so they can go in a later delivery.

**`rows.py` - the client row and honest email labels**

The 18 columns, in order: Company, Website, Company size, Industry, Location, Signal type, Job title(s), Job link, Date posted, Posted, Urgency, Score, Decision-maker, Decision-maker title, LinkedIn URL, Email, Email status, Source. Plus "Suggested opening line" when enabled. Keys starting with `_` (`_lead_id`, `_company_key`, `_job_location`, `_signal_types`, `_opening_source`) are internal and never written to client files.

Row details: up to 5 distinct job titles, then "(+N more)"; Website falls back to `https://<domain>`; "Posted" reads "posted today", "posted 5 days ago" or "date unknown (first seen ...)"; Source lists signal sources plus "contact via <finder>".

`email_label(contact)` gives exactly one of four labels, checked in this order:

| Order | Condition | Label |
|---|---|---|
| 1 | No contact or no email | `not found` |
| 2 | Status `invalid` | `not found` |
| 3 | `is_guessed(contact)` | `guessed-unverified` (always, even if a checker said `valid`) |
| 4 | Status `valid` | `verified` |
| 5 | Anything else (`risky`, `unknown`) | `risky` |

`is_guessed` is true when: `data["email_guessed"]` is set (stage 6); or the contact came from the `pattern` finder; or the provider's own raw status (`email_status_raw`, `apollo_email_status`, `csv_email_status`) contains "guess", "extrapolat" or "pattern"; or the email is one of its own `email_candidates`. With `emails.include_unverified: false`, every non-`verified` email is blanked and labelled `not found`.

In the offline demo delivery (10 rows), the labels came out as verified 5, risky 3, guessed-unverified 1, not found 1.

**`formats.py` - the files**

File names: `<client>-hiring-signals-<YYYY-MM-DD>.<ext>` (`...-PREVIEW.<ext>` in a dry run).

| Format | Details |
|---|---|
| CSV | UTF-8 with a byte-order mark so Excel shows accents; formula-injection guard on every text cell. |
| XLSX | "Leads" sheet: header in the brand colour, frozen header, filters, column widths 10-60, clickable Website / Job link / LinkedIn links, real dates, colour-coded urgency and email status; text that looks like a formula is stored as text. "About" sheet: client, period, counts by signal and by email status, the email-label legend, column guide. |
| HTML | Self-contained one-pager (inline CSS; only external file is an optional `logo_url`): KPI tiles (leads, hot, verified emails, companies), leads by signal type, top 10 hottest leads, footer. Every value HTML-escaped; only `http(s)` links are rendered; works with 0 rows. |
| Google Sheets | `push_google_sheet`: needs `gspread`; values sent with `value_input_option=RAW` (never evaluated as formulas); written in one request, so a failed write leaves the tab unchanged. A `{date}` tab that already holds data is never overwritten: rows go to `<name>-2`, `-3`, ... (up to 50). |

**Output folder layout** (verified in this review):

```
<folder>/                                             default deliveries/<client>/<date>
  <client>-hiring-signals-<date>.csv / .xlsx / .html  <- send these to the client
  _internal/                                          <- yours, never the client's
    <run id>/rejected.csv, summary.json, opportunities.csv
    not_delivered.csv                                 every company / lead left out, and why
    qa.txt, qa.json                                   the QA summary
    preview/                                          earlier PREVIEW files moved out of the way
```

A folder that already holds a delivery (this client's same date, another client or date, or another delivery's `qa.json`) is never written into: the new files go to `<folder>-2`, `-3`, ... up to `-99`.

**`opening.py` - "Suggested opening line" (optional)**

* **Template line** (free, default): built only from facts in the row, for example "Saw Acme is hiring a Senior Accountant in Austin, TX (posted 2 days ago)." Missing facts are left out; the job's own location is used, never the company HQ.
* **AI line** (`opening_line.ai: true`): one short call per row to the playbook's `writer.provider` / `writer.model`. Only company-level facts are sent (no personal data). The answer is cleaned and rejected (template used instead) if it is empty, contains a link, email, placeholder or hashtag, or mentions a number that is not in the facts.
* **Cost cap:** before every call the worst case is estimated as `(prompt characters / 4) input tokens + max_tokens output tokens` at the model's price. If that could push the run's AI spend over `max_cost_usd` (default USD 0.50), the call is skipped and AI is switched off for the rest of the run. After a call, the real token count is charged when the client reports it, otherwise the worst case. `max_tokens` per call is 400. A non-finite cap (NaN, infinity, text) never means "no cap": the default is used. `max_cost_usd: 0` means no AI spend.
* Never raises. After `writer.max_llm_failures` (3) failures in a row, or a key / config error, AI is switched off for the run. `BudgetExceeded` -> template lines for the rest. Never called in a dry run.

**`qa.py` - the QA summary**

`QAReport` fields: `client`, `leads_found`, `with_signal`, `qualified`, `delivered`, `target`, `filtered_out`, `top_reasons` (top 5, grouped so "too small (12 employees, min 20)" and "too small (5 employees, min 20)" count as one), `duplicates_removed`, `suppressed`, `verified_email_rate`, `email_status_counts`, `warnings`, `usage_lines`; details: `client_display`, `date`, `run_id`, `dry_run`, `duplicates`, `held_back`, `hot`, `companies`, `opening`, `paid_lookups`, `max_paid_lookups`, `estimated_cost_usd`, `notes`, `folder`, `files`, `sheet_url`; property `below_target`.

Warnings cover: volume below target, no leads, paid-lookup budget reached, source errors (up to 5 listed), other run errors, AI opening-line cost cap reached, and a failed Sheets push.

### 2.10 Storage (`leadgen/store.py`)

**What this means for you:** one database file is the engine's memory. It must be backed up, because it holds each client's delivery history.

One SQLite file (`storage.path`, overridden by `--db PATH`). Several playbooks and clients can share it: rows are tagged with the playbook name, except the signal history, the verification cache and the global suppression list, which are shared.

| Table | Purpose |
|---|---|
| `runs` | One row per pipeline run: id, playbook, start / finish time, meta (dry run, limit, out dir), counts |
| `signal_history` | Every (company key, signal fingerprint, external id) ever seen, with `first_seen` / `last_seen`; drives re-post detection and ageing of undated jobs |
| `verifications` | Email-check cache: email, status, provider, checked time (read back for 30 days) |
| `leads` | Every lead: id, playbook, run id, company key and name, email, score, tier, stage, full JSON payload, created / updated / exported times |
| `lead_runs` | Which runs produced each lead |
| `events` | Stage history per lead (every stage change, with an optional note) |
| `suppression` | Global do-not-list: value + kind (`email`, `domain`, `company`, `linkedin`), reason, time |
| `replies` | (outbound) stored replies with classification payload |
| `followups` | (outbound) scheduled follow-ups: due date, reason, done flag |
| `deliveries`, `client_suppression`, `delivery_runs` | The delivery ledger (see 2.9), created by `delivery.ledger.Ledger` |

(SQLite also creates its own `sqlite_sequence` table for auto-numbered ids.)

Key behaviours: `save_lead` never moves a stage backwards and `lost` is sticky; `set_stage` is forward-only unless forced or moving to `lost`; `company_engagement` reports a loss caused only by a bounce as `lost_bounce`, so colleagues stay reachable; `due_followups` hides people suppressed after scheduling; `funnel` counts a lead at stage X for X and every earlier stage.

Suppression values are normalised: emails lower-cased, domains bare (`https://www.Acme.com/` -> `acme.com`), company names normalised (`The Acme Group, Inc.` -> `acme`), LinkedIn URLs reduced to the profile. **Difference to know:** the global list matches an email's exact domain only (checked in this review: suppressing `acme.com` globally does not block `eu.acme.com`), while a client's own list also covers subdomains.

### 2.11 Usage metering, the budget and prices (`leadgen/usage.py`)

**What this means for you:** every paid request is counted, and `--budget N` stops paid requests before they are sent, so a run can never cost more lookups than you allow. After every run and delivery you see what was used and, if you have entered your plan prices, an estimated cost.

* Every adapter reaches the network through `Adapter.http`, which wraps `ctx.http` in `MeteredHttp(inner, meter, kind, type, paid)`.
* `registry.create` sets each adapter's `paid` flag from `registry.is_paid(kind, type)`.
* Each request is counted per adapter. A request by a paid adapter is a **paid lookup**.
* When the cap is reached, `before_request` raises `BudgetExceeded` **before the network is touched** (the request is counted as "blocked", not as a call). Free adapters keep working.
* Cap: `UsageMeter.from_playbook(pb, budget)`: the `--budget` value, else `usage.max_paid_lookups`. 0 = no cap. The CLI rejects a negative `--budget`.
* AI clients report tokens with `record_llm(kind, type, model, input, output)`; providers that report remaining credits (for example MillionVerifier) call `note_credits`.
* `summary_lines()` prints: paid lookups vs cap, calls / blocked / tokens / credits / estimated cost per adapter, total estimated cost. With no paid-request price set it says "estimated cost: n/a (set usage.cost_per_call.<type> ...)".

**How `BudgetExceeded` is handled, by stage:**

| Where | Behaviour |
|---|---|
| Source | That source stops; companies already paid for are kept (Apollo, TheirStack); warning |
| Enrichment | That paid finder is switched off for the run; free finders (pattern) continue; one warning "no more contact lookups this run" |
| Verification | A paid checker is swapped for `basic`; warning |
| AI opening lines | Template lines for the rest; QA note |
| AI reply classifier (outbound) | Falls back (broad exception handling) |
| AI writer (outbound) | **Not handled gracefully**: see 2.20 |

**Prices.** There is no built-in price for data providers, because they price per plan: set `usage.cost_per_call: {hunter: 0.01, ...}` yourself (the number in this example is a placeholder, not a real price). AI token prices come from `usage.llm_price_per_mtok`, else the built-in table:

| Model id (`LLM_PRICES`) | Input USD per million tokens | Output USD per million tokens |
|---|---|---|
| `claude-fable-5-1` | 10.00 | 50.00 |
| `claude-opus-5-5` | 4.00 | 20.00 |
| `claude-opus-5` (the Anthropic default) | 5.00 | 25.00 |
| `claude-sonnet-5` | 2.00 | 10.00 |
| `claude-haiku-4-5` | 1.00 | 5.00 |
| anything else (including every OpenAI model) | 10.00 (fallback) | 50.00 (fallback) |

The code comment says these are Anthropic first-party list prices cached in 2026-06. They match the Anthropic model reference available to the assistant during this review (also cached June 2026). Prices change: check them before relying on cost estimates. A model id also matches a table entry when it starts with that id plus `-`. Unknown models get the deliberately high fallback so cost caps err on the safe side.

### 2.12 HTTP layer (`leadgen/http.py`)

* `HttpClient(timeout=30.0, retries=3, backoff=1.5, user_agent="leadgen/0.1")` on a `requests.Session`.
* **Retries on status:** 429, 500, 502, 503, 504 are retried up to 3 times. The wait is the `Retry-After` header (capped at 60 seconds) or `1.5 ** attempt` seconds (1.5, 2.25, 3.4). This applies to every method.
* **Retries on network errors:** GET requests are retried the same way. **No-resend rule:** any other method (a POST that may be billed) is re-sent only after a connection error or connect time-out, which the code treats as "the request never reached the provider". (Caveat: `requests` also reports a connection dropped after the request was sent as a `ConnectionError`, so a rare re-send of an already-processed POST is still possible.) A POST that timed out while waiting for the answer is **not** re-sent, because it may already have been processed and billed.
* Never retried: invalid header, invalid URL, missing or invalid scheme.
* Final non-2xx responses raise `HttpError(status, url, body)` unless `raise_for_status=False`. Network failures become `HttpError(0, ...)`.
* **Redaction:** `redact(text)` masks `api_key=`, `apikey=`, `app_key=`, `app_id=`, `token=`, `access_token=`, `key=`, `api=`, `secret=` values and `Bearer` tokens. `safe_url(url)` also masks token-like URL path segments (16+ characters, or 5+ with a digit), such as Slack hook paths. `HttpError` messages, logged URLs and exception text all go through these. An invalid-header error never quotes the header (it would contain the key).
* Tests replace the client with `tests.fakes.FakeHttp`, which has the same interface; the CLI builds its client only through `cli.make_http()`, so tests can swap it.

### 2.13 Adapter registry and plugins (`leadgen/registry.py`)

**What this means for you:** the playbook names each tool by a short `type`, like `type: adzuna`. The registry turns that name into the right code. `leadgen adapters` lists them all.

* Kinds: `source`, `finder`, `verifier`, `llm`, `writer`, `exporter`, `notifier`.
* `_REGISTRY[kind][type] = "module:Class"`; modules are imported lazily (a missing optional package in one adapter never breaks the others).
* `create(kind, config, ctx)` builds `Class(config, ctx)` and sets `adapter_kind`, `type_name` and `paid`.
* `register(kind, type, "module:Class", paid=False)` adds or overrides an adapter. `paid=True` makes its requests paid lookups.
* `is_paid(kind, type)` = the built-in `PAID` set or a plugin registered with `paid=True`.
* `risk_note(kind, type, config)`: "use at own risk" for `linkedin_jobs`, and for `apify` with the `linkedin_jobs` / `indeed_jobs` preset or a LinkedIn / Indeed actor. Shown by `leadgen adapters`, warned by `leadgen validate`, shown by `leadgen doctor`, and logged when the Apify source runs. **No shipped playbook uses them**; `recruitment-delivery.yaml` explains why in its header.
* Plugins: `LEADGEN_PLUGINS=module_a,module_b` (shell or `.env`) imports those modules before any command; the current folder is added to Python's import path. An import failure is one clear error line (exit 2).

Built-in adapters. The "Runs" and "Credential" columns follow `leadgen adapters` (re-run on 2026-09-25), with "free key" / "no key" added for clarity (the command itself prints plain `network` for those). "Paid" in the Runs column means the type is in `registry.PAID`: every request costs money or credits and counts towards `--budget` / `usage.max_paid_lookups`. The service hosts come from each module; "Used by default" is as shipped. **Every network adapter below has only been tested against canned responses; none has been called live** (see [6.5](#65-never-tested-live-every-network-adapter)).

| Kind | Type | Runs | Credential | Service host (default) | Used by default? |
|---|---|---|---|---|---|
| source | `csv`, `json` | offline | - | your own files | `csv` in the demo playbooks; a `csv` entry (`data/imports/jobs.csv`) is in `recruitment-delivery.yaml` with `enabled: false` |
| source | `adzuna` | network (free key) | `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | `api.adzuna.com` | **Yes**: the only source switched on in `recruitment-delivery.yaml` |
| source | `greenhouse` | network (no key) | - | `boards-api.greenhouse.io` | No (`enabled: false` example watchlist) |
| source | `lever` | network (no key) | - | `api.lever.co` / `api.eu.lever.co` | No |
| source | `ashby` | network (no key) | - | `api.ashbyhq.com` | No |
| source | `theirstack` | network (paid) | `THEIRSTACK_API_KEY` | `api.theirstack.com` | Off in delivery; **on** in `my-agency.yaml` |
| source | `apollo` | network (paid) | `APOLLO_API_KEY` | `api.apollo.io` | No |
| source | `apify` | network (paid) | `APIFY_TOKEN` | `api.apify.com` | No (its `linkedin_jobs` / `indeed_jobs` presets are use at own risk) |
| source | `linkedin_jobs` | network (paid), **use at own risk** | `APIFY_TOKEN` | via Apify | **Never** |
| finder | `csv` | offline | - | your own file | The demo |
| finder | `pattern` | offline | - | - | **Yes** (the only finder on in `recruitment-delivery.yaml`) |
| finder | `apollo` | network (paid) | `APOLLO_API_KEY` | `api.apollo.io` | Off in delivery; on in `my-agency.yaml` |
| finder | `hunter` | network (paid) | `HUNTER_API_KEY` | `api.hunter.io` | Off in delivery; on in `my-agency.yaml` |
| verifier | `basic` | offline | - | - | **Yes** (the default checker) |
| verifier | `hunter` | network (paid) | `HUNTER_API_KEY` | `api.hunter.io` | No |
| verifier | `millionverifier` | network (paid) | `MILLIONVERIFIER_API_KEY` | `api.millionverifier.com` | Off in delivery; on in `my-agency.yaml` |
| verifier | `zerobounce` | network (paid) | `ZEROBOUNCE_API_KEY` | `api.zerobounce.net` (also `api-us.` / `api-eu.`) | No |
| verifier | `neverbounce` | network (paid) | `NEVERBOUNCE_API_KEY` | `api.neverbounce.com` | No |
| llm | `anthropic` | network (paid) | `ANTHROPIC_API_KEY` | `api.anthropic.com/v1/messages` | Only for AI opening lines (off by default) and in `my-agency.yaml` |
| llm | `openai` | network (paid) | `OPENAI_API_KEY` | `api.openai.com/v1` | No |
| llm | `openai_compatible` | network (paid) | the variable named in `writer.api_key_env` | any `base_url` (the code mentions OpenRouter as an example) | No |
| writer | `template` (offline), `ai` (network, through the llm) | outbound mode only | - | - | `ai` in `my-agency.yaml` |
| exporter | `csv`, `json` | offline | - | your own folder | `csv` (the review file `opportunities.csv`) is the engine default |
| exporter | `gsheets` | network | `GOOGLE_APPLICATION_CREDENTIALS` (or `GOOGLE_SERVICE_ACCOUNT_JSON`) | Google, through the `gspread` library | No |
| exporter | `instantly_csv`, `smartlead_csv` | offline, outbound mode only | - | upload files | `instantly_csv` in `my-agency.yaml` |
| exporter | `instantly` | network, outbound mode only | `INSTANTLY_API_KEY` | `api.instantly.ai` | No (commented out in `my-agency.yaml`) |
| exporter | `smartlead` | network, outbound mode only | `SMARTLEAD_API_KEY` | `server.smartlead.ai` | No |
| exporter | `webhook` | network, outbound mode only | `LEADGEN_EXPORT_WEBHOOK_URL` | your URL | No |
| notifier | `console` | offline | - | - | **Yes** (default) |
| notifier | `slack` | network | `SLACK_WEBHOOK_URL` | `hooks.slack.com` | No (commented out) |
| notifier | `webhook` | network | `LEADGEN_WEBHOOK_URL` | your URL | No |

Note: the Instantly and Smartlead exporters are **not** in `registry.PAID`, so `--budget` does not cap them (they are subscriptions; see [3.11](#311-outbound-tools-kept-for-later)).

### 2.14 `leadgen doctor` (`leadgen/doctor.py`)

**What this means for you:** run `leadgen doctor --client NAME` (or `-p playbook.yaml`) to find out whether your API keys work, without spending anything.

* Builds every enabled source, finder, checker, exporter and notifier (plus the writer's AI model when `writer.provider` is set, and a client's Google Sheet when configured) and checks each key with **one call to a free endpoint**: an account, credit-balance, health or model-list endpoint. It never searches, enriches, verifies or asks an AI model to write.
* Statuses: `ok` (with quota where reported), `failed`, `missing_key`, `skipped` (no key needed; Slack / webhooks, because a test would post a real message; hand-over exporters in delivery mode; or a dry run).
* Requests go through the **un-metered** `ctx.http`, so they are never paid lookups and never blocked by `--budget`.
* Identical requests (for example an Apollo source and an Apollo finder with the same key) are made once. Every message is redacted and each key used is masked literally (some providers echo it back).
* `--dry-run`: only checks that keys are present, contacts nobody. Checked in this review against `recruitment-delivery.yaml`: Adzuna reported `MISSING KEY`, the rest `skipped`, exit code 1.
* Endpoints (defaults; `doctor_url` in an adapter's config overrides): Apollo `/v1/auth/health`; Hunter `/account`; MillionVerifier `/credits`; ZeroBounce `/getcredits`; NeverBounce `/account/info`; TheirStack `/v0/billing/credit-balance` (the code marks this one **UNCERTAIN**, as it is less formally documented); Adzuna a 1-result search (there is no account endpoint); Apify `/users/me`; Anthropic `GET /v1/models/{model}` (also confirms the model exists); OpenAI `GET /models`; Instantly a 1-campaign list; Smartlead the campaign list; Google Sheets a local check of the service-account key and that `gspread` is installed.
* `CHECKS[(kind, type)]` maps each adapter type to its check function, so a plugin can add one.

### 2.15 Outbound-only parts: replies and the webhook server

Kept, tested, switched off by default. Used by the owner only through `my-agency.yaml`.

**`replies.py`** - `handle_reply(reply, ctx)`: refuse unless outbound -> skip if already stored (same playbook, sender, time and body, so re-imports and webhook retries are safe) -> classify -> clean the body -> link to a lead -> act -> alert -> save.

| Category | Action |
|---|---|
| positive | Stage replied -> positive; alert with a draft answer and booking link |
| question / other | Stage replied (automated delivery notices change nothing) |
| referral | Replied; a named address becomes a new lead (unless suppressed or already a lead) |
| timing | Replied; follow-up on the date mentioned, else in `timing_default_days` (30) |
| ooo | No stage change; follow-up the day after the return date, else in `ooo_default_days` (7) |
| negative | Replied -> lost; address suppressed; pending follow-ups cancelled |
| unsubscribe | Lost; address suppressed; follow-ups cancelled |
| bounce | The bounced address's lead -> lost; suppressed; cached as `invalid` |

Classifiers (`replies.classifier`): `rules` (deterministic, with a fixed precedence: bounce > auto-reply > unsubscribe > out-of-office > referral > negative > timing > positive > question > other; negations win), `ai`, or `auto` (rules for machine-like replies or when no AI is configured, else AI).

**`server.py`** - `leadgen serve`: standard-library, single-threaded HTTP server. `GET /health`; `POST /webhook`, `/webhook/reply`, `/webhook/instantly`, `/webhook/smartlead` (payload shape detected automatically). Binds to `127.0.0.1:8787` by default. With a token (`--token` or `LEADGEN_WEBHOOK_TOKEN`) every webhook must carry it in the `X-Leadgen-Token` header or `?token=`; compared in constant time; `?token=` is masked in logs. Bodies over 1 MB are refused (413); a missing `Content-Length` gets 411.

### 2.16 Command-line surface (`leadgen/cli.py`)

There are 15 commands: `init`, `validate`, `run`, `deliver`, `clients` (`list` / `new`), `doctor`, `demo`, `leads`, `stats`, `mark`, `suppress` (`add` / `remove` / `list`), `adapters`, and the outbound-only `replies`, `serve` and `followups`. The full reference with every flag and exit code is [4.12](#412-command-reference); one-line examples are in [Appendix B](#appendix-b-command-cheat-sheet).

Global options: `-p/--playbook`, `--db`, `-v/-vv`, `--env-file`. Exit codes: 0 ok; 1 the command ran but found a problem (for example `deliver` delivered nothing, `doctor` found a bad key); 2 usage or configuration error (one friendly line on stderr, traceback with `-v`); 130 interrupted.

### 2.17 Security and privacy measures

**What this means for you:** keys do not leak into logs or files, spreadsheets cannot be booby-trapped by scraped text, and anyone who asks not to be listed can be blocked for good. This is not legal advice: the privacy-law questions in `PLAN.md` still apply.

| Measure | Where |
|---|---|
| Secrets never in logs or errors: query keys, bearer tokens and token-like URL paths masked | `http.redact`, `http.safe_url`, `HttpError`; pipeline and finder errors pass through `redact` before being stored |
| Keys resolved lazily (config value > `<key>_env` variable > default variable); a missing key fails at first use, not at load | `context.Adapter.secret` |
| `$OPENAI_API_KEY` is only ever sent to `api.openai.com`; other endpoints need an explicitly named key | `llm/openai.py` |
| Slack and webhook URLs (which contain tokens) are never logged; webhook notifier shows only scheme + host | `notify/slack.py`, `notify/webhook.py` |
| Doctor masks every key literally | `doctor.py` |
| Formula-injection guard: a text cell starting with `=`, `+`, `-`, `@`, tab or carriage return gets a leading `'` so Excel / Sheets show text instead of running a formula | `outbound/csv_export.guard_cell`; used for client CSVs, `rejected.csv`, `not_delivered.csv`, review CSVs |
| Excel stores formula-like text as text; Google Sheets uses `RAW` input | `delivery/formats.py`, `outbound/gsheets.py` |
| HTML escaping of every value; only `http(s)` links rendered (no `javascript:`) | `delivery/formats.py` (`_e`, `link_target`), `report.py` |
| Global and per-client do-not-lists (email, domain, company, LinkedIn); a global `domain` entry also blocks every email at that exact domain (not its subdomains); client lists also block subdomains | `store.py`, `delivery/ledger.py`, `leadgen suppress` |
| Honest email labels: a guess is never "verified" | `delivery/rows.py`, and `cli.honest_demo_leads` for the prospect demo |
| AI opening lines see only company-level facts, never personal data | `delivery/opening.py` |
| Webhook server: localhost by default, constant-time token check, 1 MB limit, token masked in logs | `server.py` |
| Personal data never committed: `.env`, `*.db`, `data/*.db`, `output/`, `deliveries/` are git-ignored | `.gitignore` |
| Outbound features cannot run by accident | `modes.py` and the layered guard (2.7) |
| Demo report masks emails by default | `report.mask_email`, `leadgen demo --no-mask` |

**Gaps to know:** there is no automatic deletion of old personal data (the `leads` table keeps the full contact payload; `verifications` keeps email addresses); the database file is not encrypted; the global domain suppression does not cover subdomains; the webhook server only warns when started on a public address without a token.

### 2.18 Testing strategy (`tests/`)

**What this means for you:** 2,073 automated checks run in about half a minute. They prove the logic works against sample data. They cannot prove that the real providers answer the way their documentation says (see the sandbox caveat).

* **No test touches the network.** `tests/fakes.py` provides `FakeHttp` (routes requests to canned responses by method + URL prefix or regex, records every call, and fails the test on any unexpected request) and `FakeLLM` (canned AI answers). Adapters are tested against canned payloads in each provider's documented response shape.
* **`tests/conftest.py`**: `build_ctx` / the `make_ctx` fixture builds a validated playbook, a `FakeHttp`, an in-memory SQLite store, an explicit environment and a fixed date, `TODAY = 2026-09-24`. **The fixture defaults to `mode: outbound`**, because most module tests exercise outbound features; delivery tests pass `mode="delivery"`, and `tests/test_modes.py` pins the engine's real default (delivery). `SHIPPED_PLAYBOOKS` lists the shipped playbooks explicitly, so a playbook a user adds never breaks the tests.
* **End-to-end tests** run the real CLI (`main([...])`) on the shipped sample data with a temporary database and folders: `test_e2e_delivery.py` (the delivery business, including that `.env.example` stays complete) and `test_e2e.py` (the outbound demo, twice, then replies and stats). The sample data uses relative dates ("2 days ago"), so the tests hold on any day.
* **Result in this review:** `python -m pytest` -> 2,073 passed in 29.14 s (Python 3.11.15).

Test counts per file (`pytest --co -q`):

| Area | Files (tests) | Total |
|---|---|---|
| Delivery layer | test_delivery_client (92), test_cli_delivery (85), test_delivery_opening (59), test_deliver (53), test_ledger (50), test_delivery_playbooks (49), test_delivery_qa (44), test_delivery_formats (42), test_e2e_delivery (20) | 494 |
| Outbound features | test_replies (251), test_writer (126), test_outbound (73), test_server (21), test_e2e (7) | 478 |
| Contacts and finders | test_enrich (173), test_contacts (137) | 310 |
| Core pipeline and foundation | test_filters (73), test_scoring (59), test_signals (51), test_foundation (26), test_pipeline (8), test_modes (6) | 223 |
| CLI, doctor, reports, alerts | test_doctor (85), test_cli (60), test_notify (32), test_report (17) | 194 |
| Sources | test_sources_mapping (75), test_sources_csv (47), test_sources_apis (44), test_sources_ats (16) | 182 |
| Email checkers | test_verify (120) | 120 |
| AI clients | test_llm (72) | 72 |
| **All** | 32 files | **2,073** |

### 2.19 Extension guide: writing your own adapter

**What this means for you:** a new data source, finder or checker can be added as a small separate Python file, without changing the engine.

**Contracts** (every adapter subclasses `context.Adapter` and is built as `Class(config: dict, ctx: Context)`):

| Kind | Base class | Must implement |
|---|---|---|
| source | `sources.base.Source` | `fetch() -> List[Company]` with their `Signal`s (and `Contact`s if known). Must **not** filter by ICP or score. Set `company.sources = [self.label]` and `signal.source = self.label`. Respect `self.limit` (0 = all). |
| finder | `enrich.base.ContactFinder` | `find(company) -> List[Contact]` (must not change the company; people without email are fine; put guesses in `email_candidates`). Optional `complete(company, contact) -> Contact`. Set `Contact.source`. |
| verifier | `verify.base.Verifier` | `verify(email) -> VerificationResult(email, status, raw_status, provider, detail)`; status is `valid`, `risky`, `invalid` or `unknown`. Never raise for a bad address; network / key errors may raise. |
| llm | `llm.base.LLMClient` | `complete(system, user, json_mode=False, max_tokens=1500, temperature=None) -> str`; set `last_usage` and record tokens in `ctx.usage`. |
| writer | `writer.base.Writer` | (outbound) `write(lead) -> WriterOutput` |
| exporter | `outbound.base.Exporter` | `export(leads, out_dir) -> ExportResult`; set `scope = "outbound"` only for a hand-over to a sending tool. |
| notifier | `notify.base.Notifier` | `send(event, title, text, data)` |

**Rules:**

1. Set `name` (the type), `env_key` (default variable for the key) and `offline = True` only if it never uses the network.
2. Get keys with `self.secret()` (raises `MissingCredentialError` at first use, never in `__init__`).
3. Use only `self.http` for network calls. It is metered and budgeted; parse answers defensively (`.get`, `utils.get_path`).
4. Log with `self.log`, never `print`.
5. Optional hooks for `leadgen validate`: `required_credentials() -> [(config_key, env_var)]`, and `validate()` / `check_config()` that raise on a config problem. Optional free key check for the doctor: add a function to `doctor.CHECKS[(kind, type)]`.

**Example** (a made-up job board; tested in this review in a scratch folder, outside the repository):

```python
# my_adapters.py
from typing import List

from leadgen import registry
from leadgen.models import Company, Signal, SignalType
from leadgen.sources.base import Source

class ExampleBoardSource(Source):
    """Jobs from a (made-up) job board API."""

    name = "example_board"
    env_key = "EXAMPLE_BOARD_API_KEY"

    def fetch(self) -> List[Company]:
        key = self.secret()                     # raises MissingCredentialError lazily, at first use
        data = self.http.get_json("https://api.example-board.test/v1/jobs",
                                  params={"q": self.config.get("query", ""), "api_key": key}) or {}
        out: List[Company] = []
        for job in data.get("jobs", [])[: self.limit or None]:
            c = Company(name=job.get("company", ""), domain=job.get("domain", ""),
                        location=job.get("location", ""), sources=[self.label])
            c.signals.append(Signal(type=SignalType.JOB_POSTING, title=job.get("title", ""),
                                    url=job.get("url", ""), posted_at=job.get("posted"),
                                    external_id=str(job.get("id", "")), source=self.label))
            out.append(c)
        return out

registry.register("source", "example_board", "my_adapters:ExampleBoardSource", paid=True)
```

Use it: `LEADGEN_PLUGINS=my_adapters` in `.env`, then `{type: example_board, query: controller}` under `sources:` in a playbook. What the scratch test showed: `leadgen adapters` listed `example_board  network (paid)  EXAMPLE_BOARD_API_KEY`; with a cap of 1 paid lookup the first `fetch()` worked (1 paid lookup counted) and the second raised `BudgetExceeded` before any request. Write a test for a new adapter with `FakeHttp` (`ctx.http.add("GET", "<url>", json={...})`) in the style of `tests/test_sources_apis.py`.

**Other common extensions:**

* **New client-file key:** add it to `CLIENT_KEYS` and the parsing in `delivery/client.py`, map it in `client_playbook`, document it in `clients/_template.yaml`, add tests in `tests/test_delivery_client.py`.
* **New delivery format:** add a writer to `delivery/formats.py` using `pkg.columns` and ignoring `_` keys, add it to `FORMATS` and `DELIVERY_FORMATS`, and update `docs/DELIVERY.md`.
* **Changing a column or an email label:** these are the client contract (`delivery/rows.py`, `docs/DELIVERY.md`). Change both, and the tests that pin them.

### 2.20 Known gaps, inconsistencies and things never verified live

This table lists code-level gaps. Product and data limitations are in [6.7](#67-known-limitations), which also holds the consolidated list of documentation drift. The safe go-live plan is [6.6](#66-plan-to-go-live-safely).

| Item | Detail | Suggested change |
|---|---|---|
| **No live provider calls, ever** | Every connector was built from documentation and tested only with canned responses. | Per provider: `leadgen doctor`, then a small real delivery with `--budget 20`, then fix differences. |
| **Default Anthropic model is described two ways** | Code: `AnthropicClient.default_model = "claude-opus-5"`. The comment in `playbooks/templates/generic.yaml` says an empty model means `claude-sonnet-5`. | Fix the comment to match the code (or change the default on purpose). |
| **AI opening lines on a "thinking" model (unverified risk)** | Opening-line calls use a 400-token output budget. The code's own docs note that thinking counts against `max_tokens`, and `claude-opus-5` thinks by default. A cut-off answer falls back to the template line, and after 3 in a row AI is switched off. So the worst case is template lines, not wrong data, but AI lines might rarely appear. Not testable here. | Try live with a small cap first; if needed set `writer.llm: {effort: low}` (supported by the Anthropic client) or use a cheaper model such as `claude-haiku-4-5` for this job. |
| **AI writer and the budget (outbound only)** | `writer/ai.py` does not catch `BudgetExceeded`. Checked in this review: with a cap of 1 paid lookup, `write()` raised `BudgetExceeded`. In a run, the pipeline logs one error per remaining lead and those leads get no sequence (no template fallback). Delivery mode is not affected. | Catch `BudgetExceeded` in the AI writer and fall back to the template writer. |
| **Global domain suppression is exact-domain only** | Suppressing `acme.com` globally does not block `eu.acme.com` (verified). Client lists and `icp.exclude_domains` do cover subdomains. | Make `Store.is_suppressed` check parent domains too. |
| **Sources ignore the client's roles** | Adzuna `queries` (and similar) are set in the playbook. A client in a different niche needs its own playbook copy. | Feed `roles` into source search words (noted in `PLAN.md`). |
| **TheirStack credit endpoint** | Marked UNCERTAIN in `doctor.py`. | Confirm on first live use; set `doctor_url` if it moved. |
| **No data-retention purge** | Personal data stays in `data/*.db` indefinitely. | Add a purge command (for example "delete leads older than N days, keep ledger keys"). |
| **No schema migrations** | Tables use `CREATE TABLE IF NOT EXISTS`; a changed column would not be applied to an existing database. | Add a schema-version table before the first schema change. |
| **No CI** | Tests run only when someone runs them. | Run `pytest` on every push. |
| **Python floor** | `requires-python >= 3.9`; 3.9 is past end of life. | Raise to 3.10+ and test on the deployment version. |
| **Google Sheets** | `gspread` not installed here; push tested with fakes only. | Install the extra and test one real sheet before promising it. |
| **Webhook server token** | Only a warning when started on a public address without a token (outbound only). | Refuse to start without a token on a non-local address. |

---

## 3. The stack: current state and future changes

This section lists every part of the technology and business "stack" (the set of tools the business runs on). For each part it gives:

- **(a) Today:** what we use and exactly where it lives in this repo (file, adapter, environment variable).
- **(b) Cost today:** free, free tier, or paid. The paid adapters are exactly the ones in `leadgen/registry.py` `PAID`. AI prices come from `leadgen/usage.py` `LLM_PRICES`. For every other provider the repo states no price, so this section says **"check current pricing"**.
- **(c) Limits and risks today.**
- **(d) Changes needed later**, stage by stage.

**How this was checked (2026-09-25, branch `claude/modest-hopper-lue9ct`).** I read the code and ran these commands:

- `pytest`: **2073 passed** in about 24 s on Python 3.11.15.
- `leadgen adapters`, `leadgen validate -p playbooks/recruitment-delivery.yaml` and `leadgen doctor -p playbooks/recruitment-delivery.yaml --dry-run`.
- The offline demo delivery (`leadgen deliver --client demo-client`), with its database and output in a scratch folder.

Outbound network access is blocked in this sandbox. **No paid or keyed provider has been called live.** Each one was built from its documentation and tested against canned (pre-recorded, made-up) responses. PLAN.md section 6 says the same. Anything below that depends on how a real service behaves is unconfirmed until the owner runs `leadgen doctor` and a small real delivery.

### 3.0 Stages and the stack at a glance

**The four stages used below.**

| Stage | What it means | Money | People |
|---|---|---|---|
| **Stage 0 - now** | Pilots and the first 1-4 paying clients | USD 100-200 total budget | The owner, on his laptop |
| **Stage 1** | 5-10 paying clients | Paid tools paid for by client fees | The owner, with a weekly routine that is partly automated |
| **Stage 2** | 20-50 clients, or the first hire / assistant | Tools are a normal monthly cost | 2 or more people touch the system |
| **Stage 3** | Selling the software itself (SaaS = "software as a service": customers log in and use it) | Real infrastructure budget | A small team |

The client-count triggers are planning buckets, not measured thresholds. Move to the next stage when the pain shows up, not on a date.

**Summary: every component at a glance.** The last column is the first change each part needs. The changes for every stage are in each subsection, and all of them in priority order in [3.18](#318-priority-change-list).

| # | Component | Today (how it is wired) | Cost today | Biggest limit today | First change needed |
|---|---|---|---|---|---|
| 3.1 | Language and runtime | Python `>=3.9` (`pyproject.toml`), venv, `pip install -e .` | Free | Minimum versions only (`>=`), nothing pinned; 3.9 is out of support | Pin versions (Stage 1) |
| 3.2 | Core libraries | `requests`, `PyYAML`, `openpyxl`; optional `gspread`; stdlib `sqlite3`, `http.server`, `csv`, `json` | Free | Only one network request at a time | Lock file (Stage 1) |
| 3.3 | CLI | `argparse` in `leadgen/cli.py`, 15 commands | Free | Terminal only; one `deliver` per client | Deliver-all wrapper (Stage 1) |
| 3.4 | Configuration | YAML playbooks + `clients/*.yaml` + `.env` | Free | Search words live in the playbook, not in the client's `roles` | Derive queries from roles (Stage 1) |
| 3.5 | Storage | One SQLite file, `data/leadgen.db` (store + ledger) | Free | Single file on one laptop, no backup, no delete-a-person command | Backups (Stage 0) |
| 3.6 | Job-signal sources | Adzuna ON; Greenhouse/Lever/Ashby, CSV, TheirStack OFF; Apollo, Apify available | USD 0 (Adzuna free key) | One free source; never tested live; resale terms not checked | Live smoke test + terms check (Stage 0) |
| 3.7 | Contact finders | `pattern` ON; Hunter, Apollo OFF; `csv` for your own lists | USD 0 | Free stack names **nobody** for Adzuna leads | One paid finder with a budget (Stage 0-1) |
| 3.8 | Email verification | `basic` (offline) ON; MillionVerifier, ZeroBounce, NeverBounce, Hunter OFF | USD 0 | `basic` can never produce `verified` | One paid checker for higher tiers (Stage 1) |
| 3.9 | AI | Anthropic (default `claude-opus-5`), OpenAI, OpenAI-compatible; off in delivery by default | USD 0 unless switched on | Docs disagree with code on default model and price | Fix docs, use a cheap model (Stage 0) |
| 3.10 | Output / delivery formats | CSV, XLSX, HTML, optional Google Sheets push | Free | Sent by hand; no portal, PDF or CRM push | Shared folder or Sheet per client (Stage 0-1) |
| 3.11 | Outbound tools (kept for later) | Writer, Instantly/Smartlead, webhook, replies, `serve` | Code free; `my-agency.yaml` turns on paid tools | Offer text still pitches the old service | Rewrite the offer, add a budget (Stage 0) |
| 3.12 | Scheduling / automation | cron / Windows Task Scheduler lines in README | Free | Laptop must be on; no failure alerts | Wrapper script + alerts (Stage 1) |
| 3.13 | Hosting | The owner's laptop | USD 0 | Single point of failure for keys, data and ledger | Encrypted disk + backups (Stage 0); small server (Stage 1-2) |
| 3.14 | Monitoring and QA | QA summary, `doctor`, `validate`, `clients`, console/Slack/webhook notifiers | Free | A person must read it; QA warnings are not pushed anywhere | Push QA + exit codes to Slack (Stage 1) |
| 3.15 | Testing and CI | pytest, 2073 tests, `FakeHttp`/`FakeLLM` | Free | No CI; nothing tested against real APIs | GitHub Actions CI (Stage 1) |
| 3.16 | Security and compliance | Secret masking, do-not-lists, ToS risk flags, `.gitignore` | Free | No privacy notice, client terms, erasure or access control | Terms + privacy notice (Stage 0) |
| 3.17 | Business tooling (not in repo) | None yet | - | No domain, business email, invoicing or prospect tracker | Domain + email + invoicing (Stage 0) |

### 3.1 Language and runtime

**Today (how it is wired)**

- **Python.** `pyproject.toml` declares `requires-python = ">=3.9"`. The package is `leadgen` version `0.1.0`, built with setuptools (`setuptools>=61`). There are about 25,900 lines of Python in `leadgen/`.
- **venv.** A "virtual environment" is a private copy of Python just for this project. The README Quick start creates one: `python3 -m venv .venv`, then `source .venv/bin/activate` (Windows: `.venv\Scripts\activate`).
- **Install.** `pip install -e .` installs the `leadgen` command in "editable" mode, so code changes take effect without reinstalling. The command comes from `[project.scripts] leadgen = "leadgen.cli:main"`. `python -m leadgen ...` also works (`leadgen/__main__.py`).
- **Extras.** `pip install -e ".[dev]"` adds `pytest>=7`. `pip install -e ".[sheets]"` adds `gspread>=5`.
- **`requirements.txt`** repeats the core packages plus pytest. It does not include gspread.
- **Sandbox check:** Python 3.11.15. The `leadgen/__pycache__` folder holds compiled files for 3.9, 3.10 and 3.11, so the code has run on those versions at some point. Nothing checks this automatically.

**Cost today:** free.

**Limits and risks today**

- **Loose versions.** Every dependency is a minimum (`>=`), and there is no lock file. A lock file records the exact versions that are known to work. Without one, a future release of a library could change behaviour on a new laptop or server.
- **Python 3.9 is out of support.** Python's release schedule ended security support for 3.9 in October 2025. The code still declares 3.9 as the minimum.
- **Nothing packaged.** There is no Dockerfile and no release process. A "container" such as Docker packages the code with its exact environment, so it runs the same on any machine.

**Changes needed**

| Stage | Change | Why |
|---|---|---|
| 0 | Use Python 3.11 or 3.12 on the laptop, inside the venv. Nothing else. | Newer, supported, and what the tests were run on here. |
| 1 | Add a lock file (for example `pip freeze > constraints.txt`, or pip-tools) and install from it. Decide whether to raise the minimum to 3.10+ once CI tests it. | The laptop and any server run the same versions. |
| 2 | Add a Dockerfile, tag releases (bump `0.1.0`) and keep a short CHANGELOG. | A hire or a server gets an identical setup in one command. |
| 3 | Split into a core library plus a web service, pin the Python version in the container image, and add automatic dependency updates (e.g. Dependabot or Renovate). | Security updates and a reproducible production build. |

### 3.2 Core libraries

**Today (how it is wired)**

| Library | Version rule | What it does here | Where |
|---|---|---|---|
| `requests` | `>=2.28` (2.33.1 installed) | Every network call. It is wrapped once in `HttpClient`: 30 s timeout, 3 retries, backoff `1.5^attempt`, and `Retry-After` honoured up to 60 s on 429 / 5xx errors. Adapters must never import `requests` directly (README "Extending"). | `leadgen/http.py` |
| `PyYAML` | `>=6.0` (6.0.1) | Reads playbooks and client files. | `leadgen/playbook.py`, `leadgen/delivery/client.py`, `leadgen/cli.py` |
| `openpyxl` | `>=3.1` (3.1.5) | Writes the Excel workbook. Imported only inside the function that needs it, so a missing copy breaks only `.xlsx`. | `leadgen/delivery/formats.py` |
| `gspread` | optional extra `sheets`, `>=5` | Google Sheets push and the `gsheets` exporter. Imported only when used, with a clear error if missing. **Not installed in this sandbox**: the tests use a fake module. | `leadgen/delivery/formats.py`, `leadgen/outbound/gsheets.py` |
| stdlib `sqlite3` | built in | The database. | `leadgen/store.py`, `leadgen/delivery/ledger.py` |
| stdlib `http.server` | built in | The outbound reply webhook server: `HTTPServer` + `BaseHTTPRequestHandler`, single-threaded. | `leadgen/server.py` |
| stdlib `csv`, `json`, `html`, `hmac`, `argparse`, `logging`, `difflib` | built in | CSV and JSON in and out; HTML escaping; token comparison; the CLI; logs; "did you mean" hints. | throughout |
| `.env` reader | own code | A small, dependency-free `KEY=VALUE` reader. There is no python-dotenv. | `leadgen/cli.py` |

**Cost today:** free (all open source).

**Limits and risks today**

- **One request at a time.** All HTTP is synchronous. At 25-50 leads per client this is fine. At hundreds of verifications per run it gets slow.
- **Single-threaded webhook server.** `server.py` handles one request at a time. That is fine for reply webhooks at low volume, and outbound mode only.
- **No lock file** (see 3.1).
- **Strength:** there are very few dependencies, which means less to break and less to secure.

**Changes needed**

| Stage | Change | Why |
|---|---|---|
| 0 | None. | Works as is. |
| 1 | Lock versions (same as 3.1). Keep the dependency list short. | Reproducible installs. |
| 2 | Only if runs get slow: add limited parallel calls for verification and enrichment (a small thread pool that respects each provider's rate limit). | Run time at 20-50 clients. |
| 3 | A web framework and database layer for the product (options such as FastAPI or Django, SQLAlchemy + Alembic migrations; **not decided, not built**), plus a task queue. | A multi-user web product needs them. |

### 3.3 CLI (command-line interface)

**Today (how it is wired)**

- **Framework.** `argparse` in `leadgen/cli.py`. `main(argv)` returns an exit code.
- **Commands (15).**
  - Delivery: `deliver`, `clients`, `doctor`, `suppress`, `run`, `validate`, `adapters`, `init`.
  - Reports: `demo`, `leads`, `stats`.
  - Outbound or tracking: `replies`, `serve`, `followups` (these three are refused in delivery mode with exit 2 by `outbound_gate`), and `mark` (moves a lead's stage by hand).
- **Global options.** `-p/--playbook`, `--db`, `-v/-vv`, `--env-file`.
- **Exit codes.** `0` ok, `1` the command ran but found a problem (e.g. nothing delivered, a bad key), `2` usage or configuration error.
- **One HTTP factory.** `make_http()` is the only place that creates the HTTP client, so tests can swap in `FakeHttp`.

**Cost today:** free.

**Limits and risks today**

- **Terminal only.** The owner has to be comfortable with a terminal. There is no graphical interface.
- **One command per client.** `leadgen deliver --client NAME` handles one client. There is no `--all` or "deliver every client due today" option (checked with `leadgen deliver --help`).
- **"Due" is not tracked.** `leadgen clients` shows history (deliveries, last delivery, total), but it does not work out who is due this week.

**Changes needed**

| Stage | Change | Why |
|---|---|---|
| 0 | None. Keep a short weekly checklist (PLAN.md section 3). | 1-4 clients by hand is fine. |
| 1 | Add a `deliver --all` option or a small wrapper script that loops over `leadgen clients`, runs `doctor` then `deliver`, and stops on a problem. **Not built.** | Saves time at 5-10 clients. |
| 2 | A "due this week" column (from each client's delivery day), and shorter commands for an assistant. | Fewer mistakes when someone else runs deliveries. |
| 3 | The CLI becomes an admin tool. Customers use a web interface (see 3.10). | Customers won't use a terminal. |

### 3.4 Configuration

**Today (how it is wired)**

- **Playbooks** (`playbooks/*.yaml`, YAML = a plain-text settings format). They set where leads come from, how people and emails are found and checked, and how leads are scored.
  - They are loaded by `leadgen/playbook.py`. `DEFAULTS` there holds every setting with its default.
  - `${VAR}` and `${VAR:-default}` are filled in from the environment.
  - Shipped: `recruitment-delivery.yaml` (the base for real clients), `demo-delivery.yaml`, `demo-offline.yaml`, `my-agency.yaml`, and `templates/` (`generic`, `recruitment`, `saas-funding`, `local-business`, `agency-outreach`).
- **Client files** (`clients/<name>.yaml`). One per client: roles, locations, size, exclusions, `leads_per_week`, `freshness_days` (default 7), dedupe, email policy, opening lines, budget, formats and branding.
  - `leadgen/delivery/client.py` validates them. An unknown key is an error with a "did you mean" hint.
  - `client_playbook` merges the client file over its base playbook and always forces `mode: delivery`.
  - `clients/_template.yaml` documents every key. `leadgen clients new NAME` copies it.
- **`.env`** (API keys and sender details). It is copied from `.env.example`, which lists every variable. `tests/test_e2e_delivery.py` checks that list stays complete. Variables already set in the shell win. `--env-file` picks another file.
- **Plugins.** `LEADGEN_PLUGINS=module_a,module_b` loads your own adapters.

**Cost today:** free.

**Limits and risks today**

- **One niche per playbook.** The search words that decide what is **fetched** (Adzuna `queries`, TheirStack `job_titles`) are in the playbook. The client's `roles` only decide what **counts**. A client in another niche needs a copy of the playbook (PLAN.md section 6 says this is worth automating).
- **Keys in plain text.** `.env` sits unencrypted on the laptop. It is git-ignored.
- **Hand-edited YAML.** It is easy to make typos. The validation and "did you mean" hints soften this.
- **Client files are not git-ignored.** `clients/*.yaml` hold the client's contact person and their do-not-list, which is usually their own existing clients. `git check-ignore` confirms that `clients/`, `data/imports/` (your own CSV lists, which may contain personal data) and `logs/` (from the README cron example) are **not** ignored. If the GitHub repository is shared or public, that data would be exposed.

**Changes needed**

| Stage | Change | Why |
|---|---|---|
| 0 | Keep the GitHub repo **private**. Either git-ignore real client files, `data/imports/` and `logs/`, or commit only `_template.yaml` and `demo-client.yaml`. Set Adzuna `countries` / `queries` for your niche. | Client and personal data stay out of git. |
| 1 | Build source queries from each client's `roles` automatically, or keep one playbook per niche (`playbooks/delivery-<niche>.yaml`) with a naming rule. **Not built.** | Stops mismatches between what is fetched and what counts. |
| 1 | Move keys into a password manager and paste them into `.env` only on the machine that runs deliveries. | Fewer copies of secrets. |
| 2 | Keep secrets in the server's environment or a secrets store, and give each person their own keys. | Access control once more than one person is involved. |
| 3 | Settings live in the database and are edited through a web form. YAML stays for defaults. | Customers can't edit YAML. |

### 3.5 Storage (SQLite store and ledger)

**Today (how it is wired)**

- **The database.** One SQLite file (SQLite is a database stored as a single file, with no server). It is opened by `Store` in `leadgen/store.py` with `sqlite3.connect(path, check_same_thread=False)`. The path is `storage.path`, default `data/leadgen.db`, and `--db` overrides it.
- **Store tables:**
  - `runs`
  - `signal_history`, used for re-post detection
  - `verifications`, an email-check cache kept for 30 days
  - `leads`, `lead_runs`, `events`
  - `suppression`, the global do-not-list
  - `replies`, `followups`
- **The ledger** (`leadgen/delivery/ledger.py`) lives in the same file. It is the record of what each client has already received. Its tables:
  - `deliveries (client, kind, key, ...)`
  - `client_suppression`
  - `delivery_runs`, one row per delivery, append-only
  
  It never re-delivers the same company, job or contact unless `redelivery_days` or `dedupe` allow it.
- **Which playbook uses which file.**
  - `recruitment-delivery.yaml` and `my-agency.yaml` both use `data/leadgen.db`, so they share the global do-not-list.
  - The demos use `data/demo-delivery.db` and `data/demo.db`.
- **Files on disk.** Delivered files go to `deliveries/<client>/<date>/`, with an `_internal/` folder for your eyes only. `leadgen run` writes to `output/<playbook>/<run id>/`.
- **Git.** `*.db`, `deliveries/` and `output/` are git-ignored.

**Cost today:** free.

**Limits and risks today**

- **No backup.** Nothing in the code backs up the database (searched for "backup": none). If `data/leadgen.db` is lost, the ledger forgets what each client received and old leads get re-delivered (PLAN.md "Ledger safety").
- **No delete-a-person command.** The only `DELETE` statements are for the do-not-lists. `leadgen suppress` blocks a person from future use, but it does not remove what is already stored about them. Privacy laws may require removal ("right to erasure").
- **No retention rule.** Old runs, leads and output folders are never cleaned up automatically.
- **No encryption.** The file holds names and work emails, unencrypted.
- **No migrations.** Tables are created with `CREATE TABLE IF NOT EXISTS`. There is no schema version and no `ALTER TABLE` logic, so a change to an existing table needs a hand-written migration.
- **One writer at a time.** SQLite lets one process write at once, and no WAL mode or busy timeout is set (checked: no `PRAGMA` in the code). Two deliveries started at the same moment could hit "database is locked". This is an inference and was not tested. The README cron example already staggers clients by 5 minutes.
- **Split ledgers.** Clients whose playbooks point at different `storage.path` values get separate ledgers and do-not-lists (README Troubleshooting).

**Changes needed**

| Stage | Change | Why |
|---|---|---|
| 0 | After every delivery day, copy `data/leadgen.db` to cloud storage or an external drive, with the date in the name. Keep all real clients on one `storage.path`. Rehearse only on a **copy** of the database (README "Your first real client", step 5). | Losing the ledger means re-delivering old leads to paying clients. |
| 1 | Script the backup with SQLite's online backup (`sqlite3` `.backup`, or Python's `Connection.backup`) and run it right after deliveries. Keep several weekly copies off the laptop. Add a "forget this person" command that deletes their rows and adds them to the do-not-list, and a retention setting for old runs and output. **Not built.** | Safe backups while the file is open; privacy obligations. |
| 2 | Add a schema version plus migrations. Move to Postgres (a database server that many users and processes can write to at once) when more than one person or process writes at the same time. The ledger already stores `client` on every row, which helps later. | Several people and servers writing at once. |
| 3 | Managed Postgres with automated backups and point-in-time restore, one tenant (customer) per account with strict separation, encryption at rest, and object storage (e.g. an S3-compatible bucket) for delivery files. | A SaaS stores many customers' data. |

### 3.6 Job-signal sources

A "source" fetches companies and their job postings. Paid status is taken from `leadgen/registry.py` `PAID`. On/off is as shipped in `playbooks/recruitment-delivery.yaml`.

**Today (how it is wired)**

| `type:` | File | Key (env var) | Paid? | Shipped state | Notes |
|---|---|---|---|---|---|
| `adzuna` | `leadgen/sources/adzuna.py` | `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` (free key, developer.adzuna.com) | Free | **ON** | `countries: [us]`, 3 queries, `max_pages: 2`, so at most 6 requests per run. Gives company **name**, job, link, date and location. No website, no people. |
| `greenhouse`, `lever`, `ashby` | `leadgen/sources/ats.py` | none | Free | OFF (example watchlist) | Public job boards of companies you list. An ATS (applicant tracking system) is the software a company uses to post jobs. |
| `csv`, `json` | `leadgen/sources/csv_source.py` | none | Free, offline | OFF (`data/imports/jobs.csv`); ON in the demo | Your own lists; column names are detected automatically. |
| `theirstack` | `leadgen/sources/theirstack.py` | `THEIRSTACK_API_KEY` | **Paid** (playbook: "Free trial credits, then PAID per job returned") | OFF | Jobs with company data and often the hiring team. |
| `apollo` (source) | `leadgen/sources/apollo.py` | `APOLLO_API_KEY` | **Paid** | Not in the delivery playbook | Company search plus funding / headcount signals. Job postings cost one extra call per company. |
| `apify` | `leadgen/sources/apify.py` | `APIFY_TOKEN` | **Paid** | Not in any default playbook | Runs any Apify scraper. The `linkedin_jobs` / `indeed_jobs` presets are flagged **use at own risk**. |
| `linkedin_jobs` | `leadgen/sources/apify.py` | `APIFY_TOKEN` | **Paid** | Never in a default playbook | **Use at own risk**: scraping LinkedIn is against its terms (`registry.RISKS`). |

**Cost today:** USD 0 per delivery on the shipped setup (Adzuna free key, other sources free or off). TheirStack, Apollo and Apify: **check current pricing**. The playbook's `cost_per_call` example (`apollo: 0.05, hunter: 0.03, millionverifier: 0.004, theirstack: 0.50`) is marked in the file as **examples only, not real prices**.

**Limits and risks today**

- **Never tested live.** No source with a key has been called for real. The response formats come from documentation.
- **Resale terms not checked.** Reselling data taken from job ads or data providers may be restricted. PLAN.md section 6 says to read each provider's terms before selling a report built on it.
- **Thin volume.** One free source may be thin for a narrow niche or a small region. The QA summary warns when volume is below target.
- **Missed weeks are lost.** A client only gets jobs posted in the last `freshness_days` (default 7). Anything older is no longer "fresh" by the next run.

**Changes needed**

| Stage | Change | Why |
|---|---|---|
| 0 | Get the free Adzuna key, then run `leadgen doctor`, then a real delivery for a made-up sample client. Set `countries` / `queries` for your niche. Build free Greenhouse / Lever / Ashby watchlists of local employers. **Read Adzuna's terms on commercial use and resale.** | Free volume, and first proof that the live API matches the canned tests. |
| 1 | Build queries from client `roles` (see 3.4). Switch on TheirStack for clients whose fee covers it, with `budget.max_paid_lookups` and a real `usage.cost_per_call`. Save one real (anonymised) response per source as a test fixture. | More volume and company data at a known cost per lead. |
| 2 | Add sources as plugins (`LEADGEN_PLUGINS`, `registry.register(..., paid=True)`). Fetch once per niche and share the result across clients on the same niche (**not built**). Track the health of each source over time. | Fewer credits and early warning when a source breaks. |
| 3 | A shared job index refreshed on a schedule and filtered per customer, backed by written data-licence agreements with providers. | A product needs licensed, reliable data. |

### 3.7 Contact finders

A "finder" names the decision-maker at a company and, where possible, their email. Finders run in order (the "waterfall", `leadgen/contacts.py`) until a target buyer with a usable email or email guesses is found.

**Today (how it is wired)**

| `type:` | File | Key | Paid? | Shipped state | What it does |
|---|---|---|---|---|---|
| `pattern` | `leadgen/enrich/pattern.py` | none | Free, offline | **ON** | Guesses `first.last@domain` etc. **for a person already named**. It cannot find people. Always labelled `guessed-unverified`. |
| `csv` | `leadgen/enrich/csv_finder.py` | none | Free, offline | ON in the demo | People from your own contact list, matched by domain or company name. |
| `hunter` | `leadgen/enrich/hunter.py` | `HUNTER_API_KEY` | **Paid** (repo: "small free plan, then paid") | OFF | People and emails at a domain; email finder. |
| `apollo` | `leadgen/enrich/apollo.py` | `APOLLO_API_KEY` | **Paid** (playbook: "PAID credits (small free plan)") | OFF | People search, then an email "reveal" (`reveal_limit: 1` per company, one credit each per the playbook comment). |

**Cost today:** USD 0. Hunter and Apollo: **check current pricing**.

**Limits and risks today**

- **The biggest product gap on the free stack.** Adzuna gives no names, so with only free tools the decision-maker and email columns say `not found` (README "Know what the free setup gives you").
- **No cross-client cache.** Finder results are not stored between runs. Only email checks are cached (30 days). If two clients receive the same company, the paid lookup happens twice.
- **Contact lists need a lawful source.** A `csv` list must be one you are allowed to use.
- **Never tested live** (Hunter, Apollo).

**Changes needed**

| Stage | Change | Why |
|---|---|---|
| 0 | Decide how to sell the pilot. Either sell "hiring signal + company" with names where available, or spend a small part of the USD 100-200 on **one** paid finder (Hunter or Apollo, whichever current pricing favours). Switch it on with `budget.max_paid_lookups` and set `usage.cost_per_call` to your plan's real price. | Named decision-makers are what agencies value most. |
| 1 | Make a paid finder standard in the Starter / Growth offers (PLAN.md section 2), priced from the usage lines. Watch the cost per delivered lead. | The price must cover the lookups. |
| 2 | A persistent people cache per company domain with an expiry date, shared across clients. **Not built.** | The same company for two clients should cost one lookup. |
| 3 | Data contracts with providers that allow use inside a resold product, and per-customer metering. | Legal and cost control at scale. |

### 3.8 Email verification

**Today (how it is wired)**

| `type:` | File | Key | Paid? | Shipped state |
|---|---|---|---|---|
| `basic` | `leadgen/verify/basic.py` | none | Free, offline | **ON** (the default) |
| `millionverifier` | `leadgen/verify/millionverifier.py` | `MILLIONVERIFIER_API_KEY` | **Paid** | OFF (reports credits left) |
| `zerobounce` | `leadgen/verify/zerobounce.py` | `ZEROBOUNCE_API_KEY` | **Paid** | OFF |
| `neverbounce` | `leadgen/verify/neverbounce.py` | `NEVERBOUNCE_API_KEY` | **Paid** | OFF |
| `hunter` | `leadgen/verify/hunter.py` | `HUNTER_API_KEY` | **Paid** | OFF |

How it behaves:

- **`basic`** checks syntax and throw-away domains only. The best it can say is `unknown`, so it **never** yields `verified`.
- **Paid checkers run `basic` first**, so malformed or throw-away addresses never spend a credit.
- **Results are cached for 30 days** (except `unknown`).
- **Up to 3 addresses per person are checked** (`MAX_CANDIDATES_TO_VERIFY = 3` in `leadgen/pipeline.py`).
- **Budget fallback.** When the budget runs out, a paid checker falls back to `basic`.
- **Labels.** Every row's email gets exactly one label: `verified`, `risky`, `guessed-unverified` or `not found` (`leadgen/delivery/rows.py`). A pattern guess is **always** `guessed-unverified`, whatever a checker says.

**Cost today:** USD 0. The paid checkers: **check current pricing** (the `millionverifier: 0.004` in the playbook is an example placeholder).

**Limits and risks today**

- **Few `verified` emails.** On the free stack, `verified` only comes from a list or provider that already marks an address as verified.
- **Paying to check guesses can't upgrade the label.** A paid check on a guess helps pick **which** guess to use, but the label stays `guessed-unverified`, so the credit buys less.
- **One address per request.** No bulk-check endpoint is used.
- **Never tested live.**

**Changes needed**

| Stage | Change | Why |
|---|---|---|
| 0 | Stay on `basic`. Sell the honesty of the labels and don't promise verified emails (PLAN.md section 2). | USD 0, and the labels protect trust. |
| 1 | Pick one paid checker for Growth-tier clients. MillionVerifier reports credits left in `doctor` and in the usage lines. Set its `cost_per_call`. Offer `emails.include_unverified: false` to clients who want verified emails only. | More `verified` rows where the fee covers it. |
| 2 | Bulk verification (the providers' list APIs; **not built**). Let clients report bounces so the address is marked invalid and put on the do-not-list (**not built**). | Speed, cost, and quality feedback. |
| 3 | Verification as a metered feature per customer, with a clear "what verified means" statement in the product. | Billing and expectations. |

### 3.9 AI (large language models)

**Today (how it is wired)**

- **Clients.**
  - `leadgen/llm/anthropic.py`: the Anthropic Messages API, key `ANTHROPIC_API_KEY`, **default model `claude-opus-5`** (`default_model` in the class).
  - `leadgen/llm/openai.py`: OpenAI (default `gpt-5-mini`, `OPENAI_API_KEY`) and `openai_compatible` (needs `base_url`, `model` and `writer.api_key_env`; the OpenAI key is only ever sent to api.openai.com).
  - `llm.build_llm(ctx)` builds the client from the playbook's `writer.provider` / `writer.model`.
- **Where AI is used:**
  - **Delivery mode:** only the optional "Suggested opening line" column. This needs `opening_line: {enabled: true, ai: true}` in a client file **and** a `writer.provider` in the playbook.
    - Output is capped at 400 tokens per line (a token is roughly three-quarters of a word).
    - Spend is capped per run by `max_cost_usd` (default 0.50).
    - The model only sees company-level facts, not personal data.
    - An answer that invents a number or includes a link is thrown away and the free template line is used instead (`leadgen/delivery/opening.py`).
    - `playbooks/recruitment-delivery.yaml` sets no provider, so AI is off. Its commented example uses `claude-haiku-4-5`.
  - **Outbound mode:** the AI email writer (`leadgen/writer/ai.py`, `guardrails.py`, `max_tokens` 4000) and the reply sorter (`replies.classifier: ai | auto`). `playbooks/my-agency.yaml` uses `anthropic` / `claude-opus-5`.
- **All three AI types are paid** in `registry.PAID`. Every call counts against `--budget`.
- **Prices** come from `LLM_PRICES` in `leadgen/usage.py`. The code comment says these are Anthropic first-party list prices cached 2026-06, and to check current pricing. They are only used for estimates and caps. The full table is in [2.11](#211-usage-metering-the-budget-and-prices-leadgenusagepy): `claude-haiku-4-5` is the cheapest (USD 1 / 5 per million input / output tokens) and the code default `claude-opus-5` costs 5 / 25. OpenAI models are not in the table: set their price in `usage.llm_price_per_mtok`, or the high fallback (10 / 50) applies.

**Cost today:** USD 0, because AI is off in the delivery playbook.

For scale: the **worst-case** cost of one AI opening line, from the engine's own estimator (`worst_case_cost`, which assumes the full 400 output tokens), is about USD 0.0022 on `claude-haiku-4-5` and about USD 0.0112 on `claude-opus-5`. The per-model table, including OpenAI models at the fallback price, is in [5.7](#57-unit-economics-per-client). Real spend is usually lower; none of these figures has been checked against a live bill.

For the outbound writer, the **output alone** can reach 4000 tokens per lead: about USD 0.10 per lead on `claude-opus-5`, or USD 0.02 on `claude-haiku-4-5`. That is before input tokens and a possible retry.

**Limits and risks today**

- **Never tested live.**
- **Docs disagree with the code:**
  - `playbooks/templates/generic.yaml` says an empty model means `claude-sonnet-5`, but the code default is `claude-opus-5`.
  - The same file calls `claude-opus-5-5` "best, costs more", but `LLM_PRICES` lists it as **cheaper** than `claude-opus-5`.
  - Neither was changed here. Both need fixing (see the documentation-drift list in [6.7](#67-known-limitations)).
- **Prices go stale.** The table was cached in 2026-06.
- **Personal data goes to the AI provider in outbound mode.** The email writer sends lead details, including names. That is a data-processing question to settle before using it at scale.

**Changes needed**

| Stage | Change | Why |
|---|---|---|
| 0 | Keep AI off in deliveries (template lines are free). If a client wants AI lines, use `claude-haiku-4-5` with the default USD 0.50 cap. Fix the two doc mismatches above. `leadgen doctor` confirms the key **and** that the model exists (it calls the model-info endpoint). | Spend nothing until a client pays for it. |
| 1 | Re-check `LLM_PRICES` against the provider's price page when prices change. For `my-agency.yaml`, try the template writer or a cheaper model before `claude-opus-5`. | Accurate cost caps; a smaller AI bill. |
| 2 | Ideas, **not built:** AI to flag irrelevant jobs or agency postings before delivery; prompt caching through `writer.llm.extra_body` (the client already counts cache tokens); a discounted batch mode if the provider offers one. | Quality and cost at volume. |
| 3 | Per-customer AI metering and billing, data-processing agreements with the AI provider, and routing each task to the cheapest model that is good enough. | A product needs cost and privacy guarantees. |

### 3.10 Output and delivery formats

**Today (how it is wired)**

- **`leadgen/delivery/formats.py`** writes three files per delivery, named `<client>-hiring-signals-<YYYY-MM-DD>.<ext>`:
  - **CSV:** UTF-8 with a BOM so Excel shows accents. Cells starting with `= + - @` get a leading `'` (formula-injection guard: stops a spreadsheet running a cell as a formula).
  - **XLSX** (openpyxl): a **Leads** sheet (filters, clickable links, real dates) and an **About** sheet (counts, email-label legend).
  - **HTML:** a self-contained one-page summary with inline CSS and every value escaped. It prints to PDF from a browser.
- **Optional Google Sheets push** (`push_google_sheet`, client file `delivery.google_sheet`).
  - It needs `gspread` and a Google service account (a robot Google account that owns the key), via `GOOGLE_APPLICATION_CREDENTIALS` (a file path) or `GOOGLE_SERVICE_ACCOUNT_JSON` (the JSON itself).
  - A `{date}` tab is never overwritten.
  - Nothing is pushed in dry runs.
- **Where files go.**
  - The client files go to `deliveries/{client}/{date}/`. An existing folder is never overwritten: the next one gets `-2`, `-3`, ...
  - Internal files go to `_internal/`: `qa.txt`, `qa.json`, `not_delivered.csv` and the run folder.
- **Sales one-pager.** `leadgen demo` makes a "live opportunities" page (Markdown + HTML, emails masked) to show prospects.
- **`leadgen run` review files:** the `csv` (`opportunities.csv`), `json` and `gsheets` exporters.
- **Sending is by hand, on purpose.** leadgen never sends the report, so a person reviews it first.

**Cost today:** free. The Sheets push needs a free Google Cloud project; the Sheets API has usage quotas (check them).

**Limits and risks today**

- **Manual sending every week.**
- **PDF only via browser print.** No PDF file is generated.
- **No client portal or dashboard.**
- **No CRM push.** A CRM (customer relationship management system) is where agencies track contacts. The CSV imports into any CRM, but nothing pushes to one directly.
- **Sheets push never tested against Google.**
- **Email attachments carry personal data.** Send them only to the client's named contact.

**Changes needed**

| Stage | Change | Why |
|---|---|---|
| 0 | Send files from a business email address, or through one private shared folder per client. Before offering the Google Sheet to a client, test the push once for real. | Professional and simple. |
| 1 | Consider a generated PDF of the HTML summary (**not built**; e.g. a PDF library or headless browser). Add a "feedback" column in the client's sheet (useful / not useful / placed). | Some clients want PDF; feedback drives renewals (PLAN.md section 8). |
| 2 | **Future idea, not built:** push leads directly into the client's recruitment CRM or ATS. Examples are Bullhorn, and general CRMs such as HubSpot. It would need the client's API access and a new delivery target next to `push_google_sheet`. | Saves the agency the import step; a strong upsell. |
| 3 | **Future idea, not built:** a client portal or dashboard with login, delivery history, downloads, feedback, and settings the client can change. Plus white-label branding (the `branding` client setting already exists) and an API or webhook for customers. | The core of a SaaS product. |

### 3.11 Outbound tools (kept for later)

**Today (how it is wired)**

- **Only in `mode: outbound`.** The guard is layered: `leadgen/modes.py` (`require_outbound`), the pipeline, and the CLI's `outbound_gate`. Client deliveries are always forced to `mode: delivery` and have hand-over exporters removed.
- **The parts:**
  - Email writer: `leadgen/writer/` (`template` = free and offline; `ai` = paid).
  - Hand-over exporters in `leadgen/outbound/` (a hand-over passes leads to a sending tool):
    - `instantly_csv` / `instantly` (`INSTANTLY_API_KEY`, `INSTANTLY_CAMPAIGN_ID`)
    - `smartlead_csv` / `smartlead` (`SMARTLEAD_API_KEY`, `SMARTLEAD_CAMPAIGN_ID`)
    - `webhook` (`LEADGEN_EXPORT_WEBHOOK_URL`; a webhook is a URL that another service calls to pass data)
  - Reply handling: `leadgen/replies.py` (rules / ai / auto).
  - The reply webhook server: `leadgen serve`, `leadgen/server.py`. It listens on `127.0.0.1:8787` by default. A token (`--token` or `LEADGEN_WEBHOOK_TOKEN`) is optional: when set it is checked with `hmac.compare_digest`; without one the server still starts, unauthenticated (only a warning on a non-local address). Endpoints: `GET /health`, and `POST /webhook`, `/webhook/reply`, `/webhook/instantly`, `/webhook/smartlead`.
  - Follow-ups (`leadgen followups`) and stages (`leadgen mark`, `leadgen stats`).
- **Notifiers work in both modes:** `console`, `slack` (`SLACK_WEBHOOK_URL`) and `webhook` (`LEADGEN_WEBHOOK_URL`).
- **`playbooks/my-agency.yaml`** is the owner's tool for finding **his own** clients. As shipped it turns on:
  - `theirstack` (source)
  - `apollo`, `hunter`, `pattern` (finders)
  - `millionverifier` (checker)
  - the `ai` writer with `anthropic` / `claude-opus-5`
  - the `instantly_csv` exporter
  - `replies.classifier: auto`

**Cost today:**

- The code is free.
- `my-agency.yaml` **as shipped switches on five paid adapter types** (TheirStack, Apollo, Hunter, MillionVerifier, Anthropic). Use `--budget` and check current pricing.
- Instantly and Smartlead are subscriptions (check current pricing). They are **not** in `registry.PAID`, so `--budget` does not cap them.
- Cold email also needs separate sending domains and inboxes (see 3.17).

**Limits and risks today**

- **Old offer text.** The `offer` section in `my-agency.yaml` still pitches a lead-generation agency. The README says to rewrite `service`, `value_prop` and `proof` to pitch the Hiring Signal Report.
- **Cold-email law is the owner's responsibility** wherever he and the recipients are.
- **The webhook server needs HTTPS in front of it** (a reverse proxy or tunnel) before real tools can reach it.
- **Never tested live.**

**Changes needed**

| Stage | Change | Why |
|---|---|---|
| 0 | Rewrite the `offer` in `my-agency.yaml`. Cheapest route: run it as `mode: delivery` to get a prospect **list** only, then write short personal emails or LinkedIn messages from your own account. If you do run outbound, use `--dry-run` first, then `--limit 10 --budget 100` (README), and consider the free `template` writer or a cheaper model instead of `claude-opus-5`. | Win the first clients without burning the USD 100-200. |
| 1 | Only if cold email becomes the main way you win clients: warmed-up separate sending domains, an Instantly or Smartlead plan, and `leadgen serve` on a small server behind HTTPS with a token. | Scale your own client acquisition. |
| 2 | Keep it separate from client work. The business model is **selling reports, not running outreach for clients**. Doing outreach for clients would be a different service with different legal exposure. | Keep the product focused. |
| 3 | Move the outbound code into its own package, or drop it from the SaaS build. | A smaller, clearer product. |

### 3.12 Scheduling and automation

**Today (how it is wired)**

- **No built-in scheduler.** The README "Weekly automation" section gives example lines:
  - **cron** (the Linux / macOS task timer): one line per client, e.g. Mondays 06:45, staggered by 5 minutes, logging to `logs/deliver-<client>.log`.
  - **Windows Task Scheduler:** `.venv\Scripts\leadgen.exe` with "Start in" set to the repo folder.
- **What to watch.** Exit code `1` = nothing delivered; `2` = configuration problem. Run `leadgen doctor` weekly to catch expired keys.
- **Sending stays manual.**

**Cost today:** free.

**Limits and risks today**

- **The laptop must be on and awake** at the scheduled time.
- **No alert on failure.** Cron only emails you if mail is set up, and there is nothing else.
- **One schedule line per client.**
- **A missed week can't be caught up** past `freshness_days` (see 3.6).
- **`logs/` is not git-ignored.**
- **GitHub Actions can't keep the database** between runs on its own. GitHub Actions is GitHub's service that runs scripts on its servers. The README warns that `data/*.db` must survive between runs, or the ledger forgets.

**Changes needed**

| Stage | Change | Why |
|---|---|---|
| 0 | Run deliveries by hand on a fixed weekday (PLAN.md section 3 checklist), or add one cron / Task Scheduler line per client. | 1-4 clients take minutes. |
| 1 | One wrapper script: for each client, `doctor`, then `deliver`, then back up the database, then post a Slack message if any exit code is not 0. Add `logs/` to `.gitignore`. | Reliable weekly routine with alerts. |
| 1-2 | Run the schedule on a small always-on server (VPS, see 3.13) with cron. Use GitHub Actions **for tests (CI)**, not for deliveries, unless the database moves to a hosted database. | The ledger must persist; a laptop may be asleep. |
| 3 | A job queue and scheduler (options such as a hosted cron service, APScheduler or Celery beat: **ideas, not decided**), with retries, per-customer schedules and a run history page. | Many customers, different days, no manual steps. |

### 3.13 Hosting

**Today (how it is wired)**

- **Everything runs on the owner's laptop** (Windows, macOS or Linux): the repo folder, `.venv`, `.env`, `data/*.db`, `deliveries/`.
- **No server, container or cloud account** is used. The outbound webhook server listens on `127.0.0.1` only.

**Cost today:** USD 0 (existing laptop).

**Limits and risks today**

- **Single point of failure.** A lost, stolen or broken laptop means lost keys, lost client data and a lost ledger.
- **Availability** depends on the laptop being on.
- **Personal data sits on a personal device.**

**Changes needed**

| Stage | Change | Why | Rough cost (estimate) |
|---|---|---|---|
| 0 | Keep the laptop. Turn on full-disk encryption (FileVault on macOS, BitLocker or Device Encryption on Windows). Back up `data/*.db` and `clients/` off the laptop. Private GitHub repo for the code. | Cheapest safe setup. | USD 0 |
| 1-2 | A small Linux VPS (virtual private server: a rented always-on computer) running the venv and cron, reached over SSH with keys, with the provider's snapshot backups turned on. | Deliveries run even when the laptop is off; one place for data. | Roughly USD 5-10 per month for a small VPS (estimate; check current pricing) |
| 2 | Separate user accounts on the server for each person, and managed backups. Consider a managed Postgres when you move off SQLite. | Several people working safely. | Estimate only; check pricing |
| 3 | A cloud platform: container hosting, managed Postgres, object storage, separate staging and production environments, a custom domain with TLS (the "https" padlock). | A customer-facing product. | Depends on usage; budget it when the product exists |

### 3.14 Monitoring and QA

**Today (how it is wired)**

- **QA summary** (`leadgen/delivery/qa.py`). It is printed after every delivery and saved as `_internal/qa.txt` and `qa.json`, with `not_delivered.csv` beside them. It shows:
  - found, qualified and delivered counts vs target
  - top reasons leads were left out, duplicates and do-not-list hits
  - verified email rate and a count per email label
  - usage and estimated cost
  - warnings: 0 leads, below target, budget reached, source failed, AI cost cap hit, Sheets push failed
- **`leadgen doctor`** (`leadgen/doctor.py`) makes one call per key, to **free** account, credit, health or model-list endpoints only. It never makes a paid lookup, isn't metered, and `--budget` never blocks it. `--dry-run` only checks that keys are set.
- **`leadgen validate`** is an offline checklist of adapters, config and which keys are set.
- **History.** `leadgen clients` shows each client's history (from the `delivery_runs` table). `leadgen stats` shows the funnel and recent runs.
- **Notifiers.** `console` is the default. `slack` and `webhook` get the pipeline's `run_summary` event. The delivery layer itself sends nothing: `leadgen/delivery/` never calls `notify`, so **QA warnings are not pushed to Slack**.
- **Human check.** PLAN.md asks for a spot-check of 3-5 rows per delivery.

**Cost today:** free.

**Limits and risks today**

- **Someone must read the QA output.**
- **No alert** when a scheduled run fails or never starts.
- **No trends over time** (volume, verified rate, cost per client).
- **No bounce feedback from clients.**

**Changes needed**

| Stage | Change | Why |
|---|---|---|
| 0 | Read the QA summary every time, spot-check rows, and run `leadgen doctor` weekly or after any change. | Quality is the product. |
| 1 | Send the delivery QA summary and warnings, plus any non-zero exit code, to Slack (the incoming webhook is free to set up; check your plan) or to the `webhook` notifier. This is a small code change in `leadgen/delivery/run.py` or in the wrapper script. Add a "dead man's switch" heartbeat ping that alerts when a run did **not** happen (free tiers exist; estimate). Keep a monthly sheet of the numbers in PLAN.md section 8. | Know about problems before the client does. |
| 2 | A simple dashboard over the database (`delivery_runs`, QA JSON files) showing volume, verified rate, cost and client outcomes. Track the health of each source. Use client bounce reports as a quality signal. | Managing 20-50 clients by eye doesn't work. |
| 3 | Structured logs, error tracking (e.g. Sentry: an idea, not decided), uptime monitoring and a status page. | Production service standards. |

### 3.15 Testing and CI

**Today (how it is wired)**

- **pytest** (dev extra), configured in `pyproject.toml` (`testpaths = ["tests"]`).
  - 32 test files (`tests/test_*.py`), plus `fakes.py` and `conftest.py`.
  - **2073 tests, all passing** (about 24 s, Python 3.11.15, run on 2026-09-25). That comes from 1254 test functions before parametrisation (one function run with several inputs).
- **Fakes** (`tests/fakes.py`): `FakeHttp` and `FakeLLM` return canned responses in each provider's documented format.
  - `tests/conftest.py` `make_ctx` uses an in-memory database and a fixed date (`TODAY = 2026-09-24`).
  - The CLI's `make_http()` is swapped in tests, so **no test touches the network**.
  - `gspread` is faked.
- **No CI** (continuous integration: tests run automatically on every change). There is no `.github/` folder.
- **No linter or type checker** is configured in `pyproject.toml`.

**Cost today:** free.

**Limits and risks today**

- **Canned responses are not the real thing.** A real API may send different fields, errors or rate limits. PLAN.md lists "live checks with real keys" as the top open risk.
- **Nothing forces the tests to run** before a change lands.
- **Python 3.9 isn't tested automatically.**

**Changes needed**

| Stage | Change | Why |
|---|---|---|
| 0 | Run `pytest -q` after every code change. For each key you get: `leadgen doctor`, then a small real delivery with `--budget 20` (PLAN.md section 6). Fix whatever differs, and save an anonymised real response as a new test fixture. | Turns "built from docs" into "proven live". |
| 1 | GitHub Actions CI running `pytest` on the oldest and newest supported Python on every push, plus a linter (e.g. ruff). CI is free for public repos, and private repos get a monthly free allowance (check current limits). | Catches breakage automatically. |
| 2 | Opt-in live smoke tests with real keys, stored as GitHub Actions secrets and run weekly or by hand with a tiny budget. Branch protection and code review once someone else commits. | Detects provider changes early; protects `main`. |
| 3 | A staging environment, end-to-end tests of the web app, dependency and security scanning, and load tests. | Product reliability. |

### 3.16 Security and compliance

**Today (how it is wired)**

- **Secrets** are masked in logs and errors (`redact()` / `safe_url()` in `leadgen/http.py`), keys are read lazily, `doctor` masks the exact keys it used, the OpenAI key is only ever sent to api.openai.com, and the webhook server compares tokens with `hmac.compare_digest`, listens on `127.0.0.1`, and warns `auth: none` when no token is set.
- **Safe output files** (the CSV formula guard, Sheets values written as `RAW`, HTML escaping); **do-not-lists** (global and per client; kinds email / domain / company / linkedin) applied **before** paid lookups; **honest email labels**; **company-only facts** for AI opening lines.
- **Terms-of-service flags.** `registry.risk_note` marks `linkedin_jobs` and the Apify LinkedIn / Indeed presets "use at own risk"; they are never used in default playbooks.
- **`.gitignore`** covers `.env`, `*.db`, `data/*.db`, `deliveries/` and `output/`.
- **No legal regime is hard-coded** (README "Sources, compliance and do-not-lists").

The full list of measures, with the code that implements each one, is in [2.17](#217-security-and-privacy-measures).

**Cost today:** free.

**Gaps and risks today**

- **No privacy paperwork.** There is no privacy notice for the people listed and no written terms or data-processing terms with clients.
- **Provider terms not checked.** Nobody has yet checked each provider's terms on reselling its data.
- **No erasure or retention commands** (see 3.5).
- **No encryption at rest.**
- **No access control.** Whoever has the laptop or folder has everything.
- **Not git-ignored:** `clients/*.yaml`, `data/imports/` and `logs/`.
- This section is **not legal advice.** The report names real people and their work emails, and places such as the UK and EU regulate this (PLAN.md section 6).

**Changes needed**

| Stage | Change | Why |
|---|---|---|
| 0 (before the first paid delivery) | Read Adzuna's terms (and those of any tool you switch on) on commercial use and resale. Write short client terms covering what is delivered, what the email labels mean, no guarantees, the client being responsible for its own outreach compliance, and limits on use. Publish a short privacy notice on your website saying what you collect and how to opt out, and honour every opt-out with `leadgen suppress`. Keep the repo private. Encrypt the laptop disk. Get professional advice for your jurisdiction when you can. | Legal risk and client trust. |
| 1 | A data-processing terms template for clients, a retention rule, a "forget this person" command (**not built**), a password manager for keys, and two-factor login on every provider account. | Privacy obligations grow with volume. |
| 2 | Per-person accounts and API keys with the least access each person needs. Record who ran each delivery (`delivery_runs` records runs, not people). Confidentiality agreements with any hire or contractor. A written security checklist. | Several people handle personal data. |
| 3 | Authentication, strict separation between customers, encryption at rest and in transit, audit logs, agreements with every sub-processor (AI, data, hosting providers), a published privacy policy, and answers ready for customers' security questionnaires. | Customers will ask before they buy. |

### 3.17 Business tooling (not in the repo)

None of this is in the code, but the business needs it. All costs below are **rough estimates from general knowledge, not researched market data**. Check current pricing before you pay.

| Tool | Why | Stage 0 recommendation | Rough cost (estimate) | Later |
|---|---|---|---|---|
| Domain name | A professional address for email and website | Buy one short `.com` or local domain | Roughly USD 10-20 per year | Separate domains for cold email if outbound grows (3.11) |
| Business email | Send reports from `you@yourdomain` (`SENDER_EMAIL` in `.env`) | Google Workspace or similar, 1 user | Roughly USD 6-10 per user per month; forwarding to a free mailbox is cheaper but looks less professional | One account per hire (Stage 2) |
| One-page website | Credibility, the sample report, the privacy notice, a contact form | A free or cheap site builder | USD 0-20 per year | Part of the product site (Stage 3) |
| File sharing | Deliver files privately | One shared folder per client (included with Workspace) | Included | Client portal (3.10, Stage 3) |
| Invoicing and payments | Get paid | Stripe or PayPal invoices; Wise for clients abroad | Usually no monthly fee on basic plans, but a fee per payment (for cards often roughly 3% plus a small fixed fee) | Subscriptions billed automatically (Stage 1-2); billing inside the product (Stage 3) |
| Prospect tracker (simple CRM) | Track which agencies you contacted, replies and next steps | A Google Sheet (company, contact, status, next step, date), or a free CRM tier | USD 0 | A paid CRM at Stage 2 if the team needs it. `leadgen stats` / `mark` exist for outbound but a sheet is simpler |
| Booking link | Let interested agencies book a call (`BOOKING_LINK` in outbound) | Free tier of a scheduling tool | USD 0 | Paid tier if needed |
| Password manager | Store API keys and logins safely | A free or personal plan | USD 0-3 per month | Team plan (Stage 2) |
| Backups | Protect `data/*.db` | Your cloud drive or an external drive | USD 0 (included) | Automated server backups (3.5) |
| Legal documents | Client terms, privacy notice | Your own drafts from reputable free templates | USD 0 now; budget a professional review once revenue allows | Data-processing agreements (Stage 2-3) |

**Stage 0 budget.** How to split a USD 100-200 starting budget across these items (with a USD 100 and a USD 200 column) is in [5.8](#58-budget-allocation-for-usd-100-200); the staged plan for switching on paid tools is in [4.9.6](#496-a-starting-plan-for-a-usd-100-200-budget-assumptions). Spend on data tools only once a client's fee covers them (PLAN.md section 5).

### 3.18 Priority change list

Ordered by what to do first. Effort: **S** = hours, **M** = a few days, **L** = weeks. Costs are estimates.

| # | Change | Why | When | Effort | Cost |
|---|---|---|---|---|---|
| 1 | Back up `data/*.db` off the laptop after every delivery day | Losing the ledger means re-delivering old leads to paying clients | Stage 0 | S | USD 0 |
| 2 | Keep the GitHub repo private; git-ignore real `clients/*.yaml`, `data/imports/` and `logs/` | Client names, their do-not-lists and personal data must not leak | Stage 0 | S | USD 0 |
| 3 | Live smoke test: `leadgen doctor`, then a `--budget 20` real delivery, per key (Adzuna first); save real responses as test fixtures | No connector has ever been called live | Stage 0, then each time a tool is added | S-M | USD 0 for Adzuna; small credits for paid tools |
| 4 | Check provider terms (Adzuna first); write client terms and a privacy notice | Resale restrictions and personal-data law | Stage 0 | M | USD 0 DIY; professional review later (estimate varies) |
| 5 | Encrypt the laptop disk; add a password manager and two-factor login on provider accounts | Keys and personal data live on one device | Stage 0 | S | USD 0-3 per month (estimate) |
| 6 | Fix the model docs mismatch in `playbooks/templates/generic.yaml` (default model and "costs more" note vs code and `LLM_PRICES`) | Wrong docs lead to wrong model and cost choices | Stage 0 | S | USD 0 |
| 7 | Rewrite the `my-agency.yaml` offer; cap it with `--budget`; try the template writer or a cheaper model | Find your own clients without burning the budget | Stage 0 | S | USD 0 plus any paid lookups you allow |
| 8 | Decide on one paid finder (Hunter or Apollo) with `budget.max_paid_lookups` and a real `usage.cost_per_call` | The free stack names nobody for Adzuna leads | Stage 0-1 | S | Check current pricing |
| 9 | Build source queries from client `roles` (or a per-niche playbook rule) | Mismatch between what is fetched and what counts | Stage 1 | M | USD 0 |
| 10 | Wrapper script or `deliver --all`: doctor, deliver, back up, then alert on a non-zero exit | Weekly routine for 5-10 clients | Stage 1 | S-M | USD 0 |
| 11 | Push delivery QA warnings to Slack or webhook; add a heartbeat check | Nobody notices a failed or missing run today | Stage 1 | S | USD 0 (free tiers; estimate) |
| 12 | GitHub Actions CI (pytest on the oldest and newest Python, plus a linter) | Catch breakage automatically | Stage 1 | S | USD 0 within the free allowance (check limits) |
| 13 | Lock dependency versions | Same behaviour on laptop and server | Stage 1 | S | USD 0 |
| 14 | "Forget this person" and retention commands | Privacy obligations (erasure, storage limits) | Stage 1 | M | USD 0 |
| 15 | One paid email checker for higher tiers; `include_unverified: false` option offered | More `verified` rows where the fee covers it | Stage 1 | S | Check current pricing |
| 16 | Persistent people / finder cache shared across clients | The same company for two clients should not cost twice | Stage 1-2 | M | Saves money |
| 17 | Move scheduled runs to a small VPS with cron and snapshot backups | Runs even when the laptop is off | Stage 1-2 | M | Roughly USD 5-10 per month (estimate) |
| 18 | Schema versioning and migrations | Safe database changes once real data exists | Stage 2 | M | USD 0 |
| 19 | Access control: per-person accounts and keys, an audit of who delivered what, confidentiality agreements | First hire or assistant | Stage 2 | S-M | Estimate: password-manager team plan |
| 20 | Opt-in live smoke tests in CI with real keys; branch protection | Detect provider API changes early | Stage 2 | M | Small credits per run |
| 21 | Dashboard of volume, verified rate, cost and outcomes per client | Managing 20-50 clients | Stage 2 | M | USD 0 (self-hosted) to low monthly (estimate) |
| 22 | CRM or ATS push (e.g. Bullhorn): **future idea, not built** | Saves agencies the import step; an upsell | Stage 2-3 | L | Depends on the client's CRM access |
| 23 | Postgres plus multi-user support | Several people and processes writing at once | Stage 2-3 | L | Managed database: estimate; check pricing |
| 24 | Client portal, PDF export, customer login, billing, customer separation: **not built** | The software product (SaaS) | Stage 3 | L | Budget when the product is funded |

---

## 4. Operations manual

This section is the "how do I actually run it" part. It covers installing leadgen, the first run, setting up and serving a real client every week, reading the quality report, do-not-lists, costs, automation, backups, every command, outbound mode and troubleshooting. It ends with what will need to change as the business grows.

**How this section was checked.** Every command, flag and message quoted here was run on 2026-09-25 in a Linux sandbox (Python 3.11.15) from the repository `/home/user/Lead-Gen-`, branch `claude/modest-hopper-lue9ct`. Test databases and output folders were kept outside the repository, so the repository was left unchanged. Keep these limits in mind:

- **No live provider was ever called.** The sandbox blocks outbound calls to APIs (Adzuna, Hunter, Apollo, TheirStack, the email checkers, Anthropic, OpenAI, Google, Slack). Where this section shows output from a paid tool or from Adzuna, it came from a **simulation**. The real CLI ran, but its web requests were answered by the test suite's fake HTTP client (`tests/fakes.py`, `FakeHttp`) with made-up answers. Every such block is labelled *simulated*.
- `pip install` from PyPI (the public Python package index) **did** work through the sandbox proxy, so the installation steps were tested for real in a fresh copy of the repository.
- **Windows was not tested.** The sandbox runs Linux. The Windows notes follow standard Python and Windows behaviour and are labelled untested.
- **cron itself was not tested** (the sandbox has no `crontab` program). The commands inside the cron lines were run by hand, as a script.

Paths in the example outputs are shown as a user would see them when running from the repository folder. The date in every output is the day of the test, 2026-09-25.

### 4.1 What you need before you start

| Item | Needed for | Cost |
|---|---|---|
| A computer with **Python 3.9 or newer** and **git** | Everything. `pyproject.toml` says `requires-python = ">=3.9"`. Tested on 3.11.15. | Free |
| A text editor (VS Code, Notepad++, TextEdit in plain-text mode) | Editing `.env`, client files and playbooks (YAML files) | Free |
| A free **Adzuna** API key (developer.adzuna.com): an app ID and an app key | The default live delivery path (`playbooks/recruitment-delivery.yaml`) | Free, rate-limited |
| Optional paid keys: Hunter, Apollo, TheirStack, MillionVerifier / ZeroBounce / NeverBounce, Anthropic / OpenAI | Naming decision-makers, confirming emails, AI opening lines | Paid per request or credits. Prices were **not** researched for this document. Check each provider's current price list. |
| Optional: a Google service account + the `gspread` package | Pushing a delivery into a Google Sheet | Free (Google Cloud account needed) |
| A machine that is switched on at delivery time | Weekly automation (cron / Task Scheduler) | Your own laptop is fine at the start |

**YAML** is the settings format used by playbooks and client files: `key: value` lines, indented with spaces (never tabs), lists written `[a, b, c]`.

### 4.2 Installation

#### 4.2.1 Linux / macOS

```bash
git clone <this repo's URL> Lead-Gen-
cd Lead-Gen-
python3 -m venv .venv            # a private Python just for this project (a "virtual environment")
source .venv/bin/activate        # switch it on: your prompt starts with (.venv)
pip install -e .                 # installs the `leadgen` command ("-e" = editable: code changes apply at once)
leadgen --version                # -> leadgen 0.1.0
```

Verified in a fresh copy of the repository: `pip install -e .` installed `leadgen 0.1.0` with its three runtime libraries (`requests`, `PyYAML`, `openpyxl`). The `leadgen` command then lived at `.venv/bin/leadgen`.

Optional extras (both verified to install):

| Command | Adds | When |
|---|---|---|
| `pip install -e ".[dev]"` | `pytest` | To run the test suite: `pytest -q`. Result in the fresh copy: **2073 passed in 28.68s**. No test reaches the network. |
| `pip install -e ".[sheets]"` | `gspread` (6.2.1 was installed) | Only if a client wants deliveries pushed to a Google Sheet. |

`python -m leadgen ...` works the same as `leadgen ...` (verified with `python3 -m leadgen --version`).

**Always run leadgen from the repository folder.** `.env`, `clients/`, `playbooks/`, `data/` and `deliveries/` are all found relative to the folder you run the command in.

#### 4.2.2 Windows (untested here)

Use Command Prompt or PowerShell in the repository folder:

```bat
py -m venv .venv
.venv\Scripts\activate
pip install -e .
leadgen --version
copy .env.example .env
```

Notes (standard Windows / Python behaviour, not tested in this sandbox):

- If PowerShell refuses to run `activate` ("running scripts is disabled"), use Command Prompt instead, or allow local scripts for your user: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
- The installed command is `.venv\Scripts\leadgen.exe`. Scheduled tasks should call it by that full path.
- **Text encoding.** leadgen prints plain text to the console and does not force UTF-8 output. When the output is redirected to a log file (which is what a scheduled task does), Python on Windows may use an older encoding. Any character that encoding cannot represent then stops the command with `error: unexpected UnicodeEncodeError ...`. That was reproduced here by forcing a narrow encoding (`PYTHONIOENCODING=ascii leadgen leads ...` exits 1 on the `…` character leadgen uses to shorten table cells). **Precaution:** set the environment variable `PYTHONUTF8=1` for scheduled tasks (section 4.10.2 does this).
- Client names are matched case-insensitively on Windows and macOS disks: `--client ACME` opens `clients/acme.yaml`, and the client is still recorded as `acme` (see `leadgen/delivery/client.py`, `_as_on_disk`).

#### 4.2.3 What gets created where

| Path | Created by | In git? |
|---|---|---|
| `.venv/` | `python -m venv .venv` | No (`.gitignore`) |
| `.env` | You (copy of `.env.example`) | **No**, and it must never be committed |
| `data/*.db` | The first command that opens a playbook's database | No (`*.db` is ignored everywhere) |
| `deliveries/<client>/<date>/` | `leadgen deliver` | No (`deliveries/` is ignored). It holds personal data. |
| `output/<playbook>/<run id>/` | `leadgen run` (`leadgen replies` writes `replies_classified.csv` one level up, in `output/<playbook>/`) | No (`output/` is ignored) |
| `logs/` (if you create one for automation) | Your cron / scheduled task | **Yes, it is NOT ignored today.** Add `logs/` to `.gitignore`, or write logs outside the repository. |
| `data/imports/*.csv`, your own contact lists in `data/` | You | **Not ignored** (only `*.db` is). Keep personal-data CSVs outside the repository or add them to `.gitignore`. |

Side effect worth knowing: `leadgen validate -p <playbook>` (and other commands that open a playbook's database, such as `leadgen suppress list -p <playbook>`) creates an **empty** database file at that playbook's `storage.path`, and the `data/` folder too if it is missing (checked: after deleting `data/`, `leadgen validate -p playbooks/recruitment-delivery.yaml` recreated `data/leadgen.db`). This is harmless: an empty database holds no history.

### 4.3 The `.env` file: every variable

`.env` holds your API keys and your own details. Create it with `cp .env.example .env` (Windows: `copy .env.example .env`) and fill in only what you use.

Rules (verified with the engine's own parser, `parse_env_text` in `leadgen/cli.py`):

- One `NAME=value` per line. No spaces around `=`. Quotes are optional. `#` starts a comment, including after a value (`SENDER_EMAIL=   # e.g. ...` is read as empty).
- Leave unused variables empty (`NAME=`). **Never put a dummy value**: the engine will try to use it as a real key.
- A variable already set in your shell always wins over `.env`.
- `.env` is read from the folder you run in. `--env-file PATH` reads another file. If that named file does not exist the command stops: `error: env file not found: <path>` (exit 2).
- Keys and tokens are masked in leadgen's logs and error messages. The file itself is plain text, so keep it private.

`.env.example` lists 28 variables, plus one commented-out example (`OPENROUTER_API_KEY`). The "Default free delivery" column says what the default live setup (`playbooks/recruitment-delivery.yaml`, the base of every new client file) needs.

| Variable | What it is for | Default free delivery | Mode |
|---|---|---|---|
| `SENDER_NAME` | Your name in the report footer (`${SENDER_NAME}` in the delivery playbooks). `.env.example` pre-fills `Your Name`: change it. | **Recommended** | Both |
| `SENDER_EMAIL` | Your email in the report footer, so clients can reply to you | **Recommended** | Both |
| `SENDER_WEBSITE` | Your website in the report footer | **Recommended** | Both |
| `ADZUNA_APP_ID` | Adzuna app ID. The `adzuna` source needs both Adzuna values. | **Required** | Both |
| `ADZUNA_APP_KEY` | Adzuna app key | **Required** | Both |
| `THEIRSTACK_API_KEY` | `theirstack` source: job postings with company data and hiring teams | No (paid, off) | Both |
| `APOLLO_API_KEY` | `apollo` source (company search) and `apollo` finder (people + emails) | No (paid, off) | Both |
| `APIFY_TOKEN` | `apify` and `linkedin_jobs` sources. The LinkedIn / Indeed scrapers are "use at own risk" (against those sites' terms) and are never used by the default playbooks. | No (paid, off) | Both |
| `HUNTER_API_KEY` | `hunter` finder (people + emails at a domain) and `hunter` verifier. Small free plan, then paid. | No (off) | Both |
| `MILLIONVERIFIER_API_KEY` | `millionverifier` email checker. Also reports credits left. | No (paid, off) | Both |
| `ZEROBOUNCE_API_KEY` | `zerobounce` email checker | No (paid, off) | Both |
| `NEVERBOUNCE_API_KEY` | `neverbounce` email checker | No (paid, off) | Both |
| `ANTHROPIC_API_KEY` | AI through `writer.provider: anthropic`. Delivery: AI opening lines. Outbound: AI emails and AI reply sorting. | No (paid, off) | Both |
| `OPENAI_API_KEY` | AI through `writer.provider: openai` (only ever sent to api.openai.com) | No (paid, off) | Both |
| `OPENROUTER_API_KEY` (commented example) | Example only. For `writer.provider: openai_compatible`, you set `writer.api_key_env` to the name of whichever variable holds the key. | No | Both |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to a Google service-account JSON key file, for a client's `delivery.google_sheet` push and the `gsheets` exporter | No | Both |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | The same key, as the JSON text on one line (use one or the other) | No | Both |
| `SLACK_WEBHOOK_URL` | `slack` notifier: run summaries and alerts to a Slack incoming webhook | No | Both |
| `LEADGEN_WEBHOOK_URL` | `webhook` notifier: POST run summaries / alerts to any URL | No | Both |
| `SENDER_TITLE` | Your title in cold emails (pre-filled `Founder`) | No | Outbound |
| `SENDER_COMPANY` | Your company in cold emails (pre-filled `Your Company`) | No | Outbound |
| `BOOKING_LINK` | Calendly / Cal.com link offered to interested replies | No | Outbound |
| `INSTANTLY_API_KEY` | `instantly` exporter: pushes leads straight into an Instantly campaign | No | Outbound |
| `INSTANTLY_CAMPAIGN_ID` | Used as `${INSTANTLY_CAMPAIGN_ID}` in playbooks | No | Outbound |
| `SMARTLEAD_API_KEY` | `smartlead` exporter | No | Outbound |
| `SMARTLEAD_CAMPAIGN_ID` | Used as `${SMARTLEAD_CAMPAIGN_ID}` in playbooks | No | Outbound |
| `LEADGEN_EXPORT_WEBHOOK_URL` | `webhook` exporter: POST handed-over leads to Zapier / Make / n8n / a CRM | No | Outbound |
| `LEADGEN_WEBHOOK_TOKEN` | Shared secret for `leadgen serve` (the reply webhook server) | No | Outbound |
| `LEADGEN_PLUGINS` | Comma-separated Python modules that add your own adapter types | No | Advanced |

**Minimum for the default free delivery path:** `ADZUNA_APP_ID` and `ADZUNA_APP_KEY`. Fill in `SENDER_NAME`, `SENDER_EMAIL` and `SENDER_WEBSITE` so the report footer is right. **The offline demo needs nothing at all.**

Any keyed adapter can read its key from a differently named variable: add `api_key_env: OTHER_NAME` to that adapter's entry in the playbook (Adzuna uses `app_id_env` / `app_key_env`; the Slack and webhook notifiers use `webhook_url_env` / `url_env`).

### 4.4 First run: the offline demo client

`clients/demo-client.yaml` is a made-up client, *Northstar Finance Recruiting*. It recruits finance staff in Texas, Illinois, New York, Georgia and the UK and wants 10 leads a week. It runs on made-up sample data (`playbooks/demo-delivery.yaml`, reading `examples/data/demo_jobs.csv` and `examples/data/demo_delivery_contacts.csv`). No keys, no network, no cost. Its database is `data/demo-delivery.db`.

#### 4.4.1 Step 1: preview (dry run)

A **dry run** is a free rehearsal. Only offline sources run, the files are named `...-PREVIEW`, and nothing is recorded as delivered. Real output from a fresh install:

```
$ leadgen deliver --client demo-client --dry-run
Delivering the Hiring Signal Report for Northstar Finance Recruiting (demo-client) - DRY RUN (preview: offline sources only, nothing recorded as delivered) ...

PREVIEW files - not for the client (nothing was recorded as delivered) - in deliveries/demo-client/2026-09-25:
  demo-client-hiring-signals-2026-09-25-PREVIEW.csv   spreadsheet - opens in Excel / Google Sheets, imports into any CRM
  demo-client-hiring-signals-2026-09-25-PREVIEW.xlsx  Excel workbook - 'Leads' sheet + 'About' sheet (counts, email-status legend)
  demo-client-hiring-signals-2026-09-25-PREVIEW.html  one-page summary - open in a browser, or print to PDF

Internal files - for you, not the client - in deliveries/demo-client/2026-09-25/_internal:
  qa.txt                   this QA summary (qa.json: the same as data)
  not_delivered.csv        every company / lead left out, and why
  20260925-070621-d439cd/  the pipeline run: rejected.csv, summary.json, opportunities.csv

Delivery QA - Northstar Finance Recruiting (demo-client) - 2026-09-25 - DRY RUN (preview only)
  preview: nothing was recorded as delivered, files are named ...-PREVIEW
  ... (the rest is identical to the real demo QA summary, shown and explained line by line in 1.3.4) ...

Next: this was a preview - check the files, then make the real delivery: leadgen deliver --client demo-client
```

Exit code 0. ("ICP", the *ideal customer profile*, means the client's targeting: location, size, industry, exclusions.)

#### 4.4.2 Step 2: the real delivery

```
$ leadgen deliver --client demo-client
... (same summary as above, without the preview lines) ...
  note: the earlier PREVIEW files in deliveries/demo-client/2026-09-25 were moved to deliveries/demo-client/2026-09-25/_internal/preview (not for the client)

Next: send the files in deliveries/demo-client/2026-09-25 to Jamie Rivera <jamie@northstar-demo.com> at Northstar Finance Recruiting (not the _internal folder - that one is yours).
```

(The contact named on the `Next:` line is the made-up contact in `clients/demo-client.yaml`.)

The real delivery moves the same day's PREVIEW files into `_internal/preview/`, so the delivery folder holds only what you send. Otherwise the folder layout is the one shown in [1.3](#13-what-a-client-receives-a-walk-through-the-real-demo-output), with a `preview/` folder added under `_internal/`. The CSV header and first row are shown and explained column by column in [1.3.1](#131-the-csv-the-main-product) (the people and companies in the demo data are fictional).

#### 4.4.3 Step 3: run it again ("next week")

The **ledger** (the per-client record of everything delivered, stored in the database) makes sure nothing is sent twice:

| Run | Delivered | Folder | Exit | Why |
|---|---|---|---|---|
| 1st real | 10 (4 hot) | `2026-09-25` | 0 | 10 best leads; 2 held back over the weekly limit |
| 2nd real | 2 (0 hot) | `2026-09-25-2` | 0 | Only the 2 held back. QA: `10  already delivered to this client` and `WARNING: Low volume: 2 of 10 ...` |
| 3rd real | 0 | `2026-09-25-3` | **1** | Nothing new. Empty files are still written, with `WARNING: No leads in this delivery - the files are empty. Don't send them to the client`, then `Nothing to send this time` and `error: no leads were delivered - see the QA summary above for why` |

A delivery never overwrites an earlier delivery of the same day. The files go to `<date>-2`, `-3`, and so on, and the QA summary says so in a `note:` line.

`leadgen clients` afterwards (real output):

```
Clients in clients/: 1

client       display name                  leads/week  deliveries  last delivery  total delivered
-----------  ----------------------------  ----------  ----------  -------------  ---------------
demo-client  Northstar Finance Recruiting          10           2  2026-09-25                  12

total delivered = distinct companies sent so far (a company sent again is counted once).
No client file allows the same company, job or person to be delivered twice.
Next: leadgen deliver --client <name> --dry-run   |   new client: leadgen clients new <name>
```

The zero-lead run does not count as a delivery (2 deliveries, not 3).

**Start the demo over:** delete `data/demo-delivery.db` and `deliveries/demo-client/`.

### 4.5 Setting up a real client

#### 4.5.1 Create the client file

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

(Real output. When `--clients-dir DIR` is used, the next-step commands include it too.)

- The file name is the client's short name: lower-case letters, digits, `-` and `_`, starting with a letter or digit. `leadgen clients new "Acme Staffing"` fails with `error: client name 'Acme Staffing' is not allowed: use lower-case letters, digits, '-' and '_', starting with a letter or digit (for example 'acme-staffing')` (exit 2).
- An existing file is never overwritten: `error: clients/acme.yaml already exists - edit that file, or choose another name` (exit 1).
- The new file is an exact copy of `clients/_template.yaml`: every key is already written out with its default value and a plain-English comment, so you change values rather than uncomment lines. Files starting with `_` (the template) are never treated as clients.
- The short name is the client's key in the ledger. **Don't rename a client file after its first delivery**: the renamed client would start with an empty history and could receive everything again.

#### 4.5.2 Every client-file key

Every key is optional. Unknown keys are an error with a "did you mean" hint, and all problems are reported at once (see 4.14). Source: `leadgen/delivery/client.py` and `clients/_template.yaml`.

| Key | Default | What it does | Rules / side effects |
|---|---|---|---|
| `display_name` | `""` (= the file name) | Name printed on the report and file headers | Text |
| `playbook` | `playbooks/recruitment-delivery.yaml` | The **base playbook**: sources, finders, email checker, scoring, branding, database | Looked up as given (from the current folder), then next to the client file, then one folder above it. Empty = error. |
| `contact` | `{}` | Your contact at the client (`name`, `email`, ...). Your records only; never used to send anything. | Used in the `Next: send the files ... to <name> <email>` line. Without it you get a tip to add it. |
| `roles` | `[]` = the base playbook's list | Job titles the client fills. A job counts only when its **title** names one of them. | **Replaces** `signals.match_keywords`. A single text value counts as a one-item list. Duplicates are removed. |
| `exclude_roles` | `[]` | Jobs whose title contains one of these are ignored | **Added to** `signals.exclude_keywords` |
| `buyer_titles` | `[]` = the base playbook's list | Decision-maker titles, **best first** | **Replaces** `buyers.titles` |
| `locations` | `[]` = anywhere | Where the companies must be, e.g. `[Texas, Oklahoma]` or `[United Kingdom]` | **Always replaces** `icp.locations` (the base playbook's value never applies to a client). US state names and codes are understood (`Austin, TX` matches `Texas`). |
| `exclude_locations` | `[]` | Places to leave out | Always replaces `icp.exclude_locations` |
| `company_size` | `{min: null, max: null}` | Head-count range. `null` = no limit on that side. | Always replaces `icp.employees`. So the base playbook's `min: 10` does **not** apply to clients: set `min` here. `min` >= 0, `max` >= 1, `min` <= `max`. |
| `industries` | `[]` = any | Only these industries | Always replaces `icp.industries` |
| `exclusions.companies` | `[]` | Company names this client must never receive (e.g. their existing clients) | Checked as this client's do-not-list during the delivery. Plain names, not patterns. |
| `exclusions.domains` | `[]` | Website domains to leave out | Added to `icp.exclude_domains`. Must look like `bigclient.com`. |
| `exclusions.keywords` | `[]` | Leave out companies mentioning these (e.g. `[staffing, recruiting]`, competitors) | Added to `icp.exclude_keywords` |
| `leads_per_week` | `25` | Target volume. The file holds at most this many rows. Extra good leads are held back for later. | Whole number > 0. Also caps enrichment at `max(leads_per_week x 2, 10)` companies, or the base playbook's `enrichment.max_companies` (100 in `recruitment-delivery.yaml`) if lower. |
| `freshness_days` | `7` | Only jobs posted in the last N days (as of the delivery date) | Whole number > 0. Sets `signals.max_age_days`. **Also widens** Adzuna's `max_days_old` and TheirStack's `max_age_days` to N when they are smaller (verified in code; see the note below this table). |
| `allow_undated` | `false` | Keep jobs without a posting date? | true / false |
| `drop_reposts` | `true` | Leave out ads that look re-posted (old jobs put up again) | true / false |
| `redelivery_days` | empty (`null`) | `null` = never deliver the same company / job / person twice. `N` = it may come back after N days. | Empty or whole number > 0 |
| `dedupe` | `[company, job, contact]` | What must never repeat for this client | Values: `company`, `job`, `contact`. Remove `company` to allow **new** jobs at companies already delivered. `[]` switches de-duplication off. |
| `tiers` | `[hot, normal]` | Which urgency tiers may be delivered | Values: `hot`, `normal`, `skip`. Empty = error. |
| `emails.include_unverified` | `true` | `true` = risky and guessed emails are delivered, clearly labelled. `false` = only `verified` emails; the others show `not found`. | true / false |
| `opening_line.enabled` | `false` | Adds the "Suggested opening line" column | true / false |
| `opening_line.ai` | `false` | `false` = free template line. `true` = written by AI (paid; uses the base playbook's `writer.provider` / `writer.model`, or the client's `overrides.writer`). | true / false |
| `opening_line.max_cost_usd` | `0.50` | Most AI spend per delivery, in US dollars. After that, template lines. | Amount >= 0 |
| `budget.max_paid_lookups` | `0` | Most paid API requests per delivery run. `0` = no extra cap here (a cap in the base playbook still applies). | Whole number >= 0. `--budget N` overrides it. |
| `delivery.formats` | `[csv, xlsx, html]` | Which files to write | Any of `csv`, `xlsx`, `html`. `excel` gets a "did you mean 'xlsx'" hint. Empty = error. |
| `delivery.folder` | `deliveries/{client}/{date}` | Where the files go | Only the placeholders `{client}` and `{date}` (YYYY-MM-DD) |
| `delivery.google_sheet` | `{spreadsheet_id: "", worksheet: "{date}", service_account_file: ""}` | Optional push of the rows to a Google Sheet | Needs `gspread` and a service account. Empty `spreadsheet_id` = off. Never pushed in a dry run or for an empty delivery. |
| `branding` | `{}` | Change the report's look for this client | Keys: `brand_name`, `brand_color` (hex like `#1f4e79`), `sender_name`, `sender_email`, `website`, `logo_url`, `footer` |
| `overrides` | `{}` | Advanced: change any base-playbook section for this client only, merged in last | Sections allowed: `description`, `offer`, `icp`, `signals`, `buyers`, `sources`, `enrichment`, `scoring`, `writer`, `outbound`, `replies`, `notify`, `storage`, `delivery`, `usage`. `mode` and `name` are refused. `sources` must be a list. |

Whatever the client file says, a client delivery always:

- runs in **delivery mode** (nothing is written or sent to anyone), under the playbook name `client-<name>`;
- names **one** decision-maker per company (`buyers.max_contacts_per_company: 1`);
- never builds a hand-over exporter (Instantly, Smartlead, webhook exporters).

**Documentation mismatch found** (also in the drift list in [6.7](#67-known-limitations)). `README.md`, `clients/_template.yaml` and the playbook comments say a client file *cannot* widen a source's own date window. The code does widen it (`client_playbook` in `leadgen/delivery/client.py`). Checked directly: with `freshness_days: 45`, Adzuna's `max_days_old` became 45 (from 30) and TheirStack's `max_age_days` became 45 (from 7). It widens only, never narrows. **Cost impact:** TheirStack charges per job returned, so a client with a long freshness window on a TheirStack-enabled playbook fetches more paid jobs.

#### 4.5.3 Point the base playbook at your niche and market

The client's `roles` decide which jobs **count**. The source search words in the base playbook decide what is **fetched**. In `playbooks/recruitment-delivery.yaml` the Adzuna source is (abridged: its `label: adzuna` line and most comments left out):

```yaml
  - type: adzuna
    countries: [us]                                            # REQUIRED; e.g. [gb], [us, ca]
    queries: [accountant, financial controller, finance manager]
    what_exclude: [intern, internship]
    max_days_old: 30
    max_pages: 2                  # 50 jobs per page
```

That is 1 country x 3 queries x 2 pages = at most 6 Adzuna requests per run. For a client in another niche (nurses, drivers, engineers), copy the playbook (for example to `playbooks/delivery-nursing.yaml`), change `queries` (and `countries`), and set that client's `playbook:` line to the copy. The Adzuna adapter also passes `where`, `what_or`, `category` and raw `params` to Adzuna (documented in `leadgen/sources/adzuna.py`).

**Location matching risk (not verified live).** The Adzuna adapter uses Adzuna's `location.display_name` as the company location. In a simulated run with a made-up Adzuna answer where that name was `Austin, Travis County`, a client with `locations: [Texas]` rejected every company: `location not in ICP [Texas] (got: Austin, Travis County; US; Austin, Travis County)`. The real format of Adzuna's US location names could not be checked here. **On your first live delivery, open `_internal/not_delivered.csv`** and look for `location not in ICP` lines. If Adzuna's names don't include the state, possible fixes:

- list the cities or counties in `locations`; or
- leave `locations` empty and restrict the search with Adzuna's own `where` setting in the playbook; or
- map the location to Adzuna's area list with `mapping: {location: location.area}` on the Adzuna source. In the same simulation this made both Texas companies match (Location column: `US, Texas`). Only tested against the made-up answer.

#### 4.5.4 Check the keys: `leadgen doctor`

`leadgen doctor --client NAME` (or `-p PLAYBOOK`) tests every key the client's delivery uses with **one free call each**: an account, credits or model-list endpoint, never a paid lookup, never counted against the budget. `--dry-run` only checks that the keys are set and contacts nobody.

Without Adzuna keys (real output):

```
$ leadgen doctor --client acme
Checking the API keys of client 'acme' (Acme Staffing Ltd; base playbook playbooks/recruitment-delivery.yaml), delivery mode ...
  (one free account / credits call per key - never a paid lookup)

status       adapter           detail                                                                                          quota
-----------  ----------------  ----------------------------------------------------------------------------------------------  -----
MISSING KEY  source adzuna     missing key - set $ADZUNA_APP_ID and $ADZUNA_APP_KEY (or 'app_id' / 'app_key' in the playbook)  -
skipped      finder pattern    no key needed                                                                                   -
skipped      verifier basic    no key needed                                                                                   -
skipped      exporter csv      no key needed                                                                                   -
skipped      notifier console  no key needed                                                                                   -

0 ok, 0 failed, 1 missing key, 4 skipped
Fix the FAILED / MISSING KEY rows: API keys go in .env (see .env.example), then run the doctor again.
error: 1 adapter(s) need attention - see the table above
```

Exit 1. With (fake) keys set and `--dry-run`, the Adzuna row reads `skipped  source adzuna  dry run - key found, api.adzuna.com not contacted` and the exit code is 0.

With keys set but no internet (what the sandbox produces), the doctor retries three times, then:

```
FAILED   source adzuna     could not reach api.adzuna.com (ProxyError) - check your internet connection / proxy  -
```

A successful live check was **not** seen here. The README shows the expected form (`ok  source adzuna  keys accepted (1-result test search in US, ... jobs listed)`). That text comes from the test suite's canned answers.

A client file with a `delivery.google_sheet.spreadsheet_id` adds a Sheets row. Without credentials it reads: `MISSING KEY  delivery google_sheet 'Google Sheet push'  missing key - set $GOOGLE_APPLICATION_CREDENTIALS to the service-account JSON file (or 'service_account_file' in the config, or $GOOGLE_SERVICE_ACCOUNT_JSON)`. The doctor checks the credentials without contacting Google.

`leadgen validate -p PLAYBOOK` is the offline companion. It checks that every adapter type exists, that the settings are complete and which keys are set, and it shows the mode. For `playbooks/recruitment-delivery.yaml` without keys it prints `[FAIL] source adzuna - missing credential: set $ADZUNA_APP_ID ...` and exits 1. With `--dry-run` the same line becomes a `[warn]` and it exits 0.

#### 4.5.5 Preview, then a rehearsal on a copy

On the default playbook a dry run delivers **nothing**. Every source there is a web API and `--dry-run` skips anything that uses the network. Real output (trimmed):

```
$ leadgen deliver --client acme --dry-run
...
  companies found ............ 0
  ...
  WARNING: No leads in this delivery - the files are empty. Don't send them to the client; check the reasons above (and whether the sources returned anything) first.
  WARNING: Low volume: 0 of 25 leads delivered (below this client's weekly target). To find more, widen the roles / locations, raise freshness_days or add sources.
...
Note: nothing was found because every source uses the network and --dry-run skips them. Add a csv/json source to rehearse offline, or run without --dry-run.
error: no leads were delivered - see the QA summary above for why
```

So a dry run only proves that the client file and playbook load. To try a real run without touching the real ledger, run it on a **copy** of the database, into a scratch folder:

```bash
cp data/leadgen.db data/rehearsal.db        # Windows: copy data\leadgen.db data\rehearsal.db
leadgen deliver --client acme --db data/rehearsal.db --out output/rehearsal/{client}/{date}
```

Skip the `cp` line if `data/leadgen.db` doesn't exist yet. When a real (not dry-run) delivery with at least one lead used a `--db` other than the client playbook's database, the output ends with a `Careful:` line below the `Next:` line. (It is not printed for dry runs or zero-lead runs; see `_print_delivery` in `leadgen/cli.py`.) Real wording, with the scratch database path of the test replaced by the rehearsal paths above:

```
  Careful: this delivery used --db data/rehearsal.db, not data/leadgen.db (the database of this client's playbook, storage.path). Only the do-not-lists and delivery history stored in data/rehearsal.db were applied, and it was recorded there only. Send these files only if data/rehearsal.db is where you keep every delivery to acme - never the files of a rehearsal.
```

**Never send rehearsal files**: the real ledger doesn't know about them, so the real delivery would repeat their leads.

#### 4.5.6 The real delivery

```bash
leadgen deliver --client acme
```

What the free default path produces. This was **simulated**: the real CLI and the real `recruitment-delivery.yaml` ran, with a made-up Adzuna answer and the location mapping from 4.5.3 so the Texas jobs matched:

```
  companies found ............ 4
  with a live signal ......... 2
  match the client's criteria  2
  delivered .................. 2 (target 25), 0 hot
  ...
  verified email rate ........ 0% (0 of 2)
  email status ............... verified 0, risky 0, guessed-unverified 0, not found 2
  API usage: paid lookups 0 (no cap)
    source adzuna (free): 3 calls
```

The CSV rows had company, location, job title(s), job link, date and score, but **empty Decision-maker, LinkedIn and Email columns (`not found`)**. That matches the README's warning: Adzuna returns companies and jobs, not people. To name decision-makers you need Hunter / Apollo / TheirStack (paid), or a `csv` finder with a contact list you are allowed to use. Plan your offer and price around this (see 4.9.6).

`-p` is ignored by `deliver`, with `warning: -p is ignored by 'deliver': the client file's 'playbook:' line names the base playbook`.

#### 4.5.7 What to send the client, and what not

| Item | Send? | Why |
|---|---|---|
| `<client>-hiring-signals-<date>.csv` / `.xlsx` / `.html` in the delivery folder | **Yes** | The product. Send exactly the files the `Next:` line names. |
| A second real delivery on the same day, in `<date>-2/` | Yes, if it has leads | A real, recorded delivery of new leads |
| A zero-lead delivery folder (e.g. `<date>-3/` above) | **No** | Empty files. The QA summary says "Don't send them to the client". |
| `_internal/` (qa.txt, qa.json, not_delivered.csv, the run folder, preview/) | **No** | Yours. `opportunities.csv` there lists every lead **including ones not delivered**, with scores and emails. `not_delivered.csv` names companies on the client's do-not-list. |
| `...-PREVIEW.*` files | **No** | Nothing was recorded, so the real delivery could repeat them |
| Files from a rehearsal (`--db` copy, `output/rehearsal/...`) | **No** | The real ledger doesn't know about them |
| Anything else that happens to be in the folder | **No** | leadgen names it for you. Real output: `Next: send ONLY the 3 files listed above to ... <folder> also holds notes.txt: not part of this delivery (never recorded as delivered) - don't send it, and not the _internal folder either (that one is yours).` |

leadgen never sends anything itself. Attach the files to an email, share a folder (a shared cloud folder per client works), or use the Google Sheets push.

### 4.6 The weekly routine

Time estimates below are **assumptions** for planning, not measurements. A delivery itself takes seconds offline. Live runs depend on the providers.

| When | Step | Command / action | What to look for | Rough time (assumption) |
|---|---|---|---|---|
| Monday, early (or automated, 4.10) | 0. Back up | Copy `data/*.db` (4.11) | The backup file exists | 1 min |
| | 1. Keys | `leadgen doctor --client NAME` (weekly or monthly) | Every row `ok` or `skipped`; exit 0 | 1 min |
| | 2. Deliver | `leadgen deliver --client NAME` for each client | Exit 0; the `Next:` line | 1-2 min per client |
| | 3. QA review | Read the QA summary (screen, log, or `_internal/qa.txt`) | Delivered vs target, top reasons, verified email rate, every `WARNING:` line (4.7) | 3-5 min per client |
| | 4. Spot-check | Open 3-5 rows: click the job links, check the decision-maker still works there, check a couple of emails' domains match the company | Dead links, wrong person, a company the client already works with | 10-15 min per client |
| | 5. Fix before sending | Wrong company -> `leadgen suppress add "Name" --kind company --client NAME`; a person who asked not to be listed -> global `suppress add`. Re-running the same day creates a `-2` folder with new leads only. | | varies |
| | 6. Send | Email / share only the files the `Next:` line names (4.5.7) | Never `_internal/`, PREVIEW or rehearsal files | 5 min per client |
| | 7. Log | Keep a delivery log (a spreadsheet): date, client, folder, delivered / target, verified rate, warnings, sent at, client feedback. `_internal/qa.json` holds the same numbers as data. `leadgen clients` shows totals. | | 2 min |
| During the week | 8. Feedback | Change the client file (`roles`, `locations`, `exclusions`, `leads_per_week`). Put opt-out requests on a do-not-list the same day. | | as needed |
| Monthly | 9. Review | `leadgen clients` (volume trend), the usage lines (costs), the share of "already delivered to this client" (a niche running dry), and client outcomes (calls, placements) | | 30 min |

Exit codes to act on: **0** = files ready to review; **1** = nothing delivered or every source failed (read the summary, don't send); **2** = a command or client-file problem (one `error:` line says what).

### 4.7 Handling the QA summary

The QA summary is printed after every delivery and saved as `_internal/qa.txt` (text) and `_internal/qa.json` (data). Source: `leadgen/delivery/qa.py`.

#### 4.7.1 Each line and what to do about it

| Line | Meaning | What to do |
|---|---|---|
| `companies found` | Companies the sources returned, after merging the same company from two sources | Very low or 0: a source failed (see warnings), a key is missing, or the playbook's search words / countries are too narrow |
| `with a live signal` | ...with at least one fresh job whose title matches the client's `roles` | A big drop from "found": `roles` too narrow for the titles the sources return, or `freshness_days` too short. Check `no signal matching [...]` in the top reasons. |
| `match the client's criteria` | ...that also pass location / size / industry / exclusions / the global do-not-list | A big drop: `location not in ICP`, `too small`, `excluded ...`. See `not_delivered.csv` for the exact values. |
| `delivered (target N), M hot` | Rows in the file vs `leads_per_week`; M rows with urgency `hot` | Below target -> 4.7.3 |
| `filtered out` + `top reasons` | Everything left out, grouped (details and numbers stripped, e.g. `location not in ICP [...]`) | Full per-company detail is in `_internal/not_delivered.csv` and the run's `rejected.csv` |
| `duplicates removed` | Already delivered to this client (company / job / contact), plus the same company or person twice in this run | Growing every week = the niche is being used up (4.7.3) |
| `on a do-not-list` | Removed by the global list, the client's list, or the client file's `exclusions` | Expected. A sudden jump = check you didn't add a too-broad domain or keyword. |
| `held back (over the limit)` | Good leads over `leads_per_week`. Not recorded, so they can go out next time. Only shown when above 0. | Many held back every week: you could raise `leads_per_week` (or sell a bigger plan) |
| `verified email rate` | `verified` emails / rows delivered. `n/a (no leads)` when nothing was delivered. | Low -> 4.7.4 |
| `email status` | Count of each label: verified, risky, guessed-unverified, not found | Explain the labels to the client (the Excel "About" sheet has the legend) |
| `opening lines` | AI vs template lines and their cost. Only shown when the column is enabled. | A cost-cap warning -> raise `opening_line.max_cost_usd` or accept template lines |
| `API usage: paid lookups X/Y` + one line per adapter | Paid requests used / cap (`(no cap)` when none); calls per adapter; credits left (when the provider reports them); estimated cost (when `usage.cost_per_call` is set) | 4.9 |
| `note: ...` | Information: files moved to `-2`, PREVIEW files moved, all paid lookups of the budget used | Read once |
| `WARNING: ...` | See 4.7.2 | Act before sending |

#### 4.7.2 The warnings

| Warning (exact start of the text) | Cause | Action |
|---|---|---|
| `No leads in this delivery - the files are empty. Don't send them to the client; ...` | 0 rows | Don't send. Read the top reasons. Check whether the sources returned anything. |
| `Low volume: X of N leads delivered (below this client's weekly target). ...` | Fewer rows than `leads_per_week`. Ends with `Most matches were already delivered to this client in earlier weeks.` when duplicates are at least half of the qualified companies. | 4.7.3 |
| `Paid-lookup budget reached (X of Y paid lookups used): some contacts / emails were not looked up with the paid providers. Raise budget.max_paid_lookups in the client file or run with --budget N.` | The cap stopped paid finders; the verifier fell back to the free basic check | Decide whether the extra spend is worth it. Re-running the same day uses a new `-2` folder and only picks up leads not delivered yet. |
| `Source problem - source <name>: ... Leads from this source are missing this week.` | A source failed (missing key, network, provider error) | Fix the key (`leadgen doctor`), then decide whether to deliver now or later |
| `N other error(s) during the run, e.g. ... - details in <run folder>/summary.json` | Non-source errors (e.g. one email check failed) | Look at `summary.json`. Usually safe to send after a spot-check. |
| `AI opening-line cost cap reached ($X): N line(s) use the free template instead. ...` | `opening_line.max_cost_usd` would have been exceeded | Accept, or raise the cap |
| A Google Sheets push failure | The push failed. The files are still complete. | Fix the credentials / sharing; send the files meanwhile |

#### 4.7.3 When volume is low

Work through these in order. Each step is a real setting or command.

1. **Read the top reasons and `_internal/not_delivered.csv`.** They tell you which filter removed most companies.
2. **Most matches were already delivered** (the warning says so): the niche and region are being used up. Options:
   - widen `roles` (more job titles) or `locations`;
   - remove `company` from `dedupe`, so companies already delivered can come back **with a new job** (jobs and people already sent still never repeat);
   - set `redelivery_days` (e.g. 90) if the client is happy to see a company again after that time;
   - agree a lower `leads_per_week` with the client (sell a target, not a guarantee).
3. **`no signal matching [...] in last N days`** dominates: raise `freshness_days` (e.g. 7 -> 14; it widens Adzuna's and TheirStack's windows, see 4.5.2), or add job titles to `roles`. `allow_undated: true` keeps jobs without a date (they show `date unknown (first seen N days ago)`).
4. **`companies found` is low**: the sources fetch too little. In the base playbook: add Adzuna `queries`, add `countries`, raise `max_pages` (each extra page is one more free request per country x query), or switch on the free Greenhouse / Lever / Ashby watchlists (companies' own job boards) or a `csv` source with your own job list. Paid option: TheirStack.
5. **`location not in ICP` / `too small`** dominate: check the values in `not_delivered.csv` (see the Adzuna location risk in 4.5.3). Loosen `company_size.min` if the client agrees.
6. Last resort, not recommended: add `skip` to `tiers`. Those are weak leads (nothing fresh).

#### 4.7.4 When the verified-email rate is low

First understand where "verified" can come from (`leadgen/delivery/rows.py`). The client-facing wording of the four labels is in [1.3.2](#132-the-excel-workbook-xlsx) and the exact rule order in [2.9](#29-the-delivery-layer-leadgendelivery). In short: `verified` needs confirmation by an email checker (MillionVerifier, ZeroBounce, NeverBounce, Hunter) or by the provider or list that supplied the address, **and** an address that was not built from a name pattern. A pattern guess is **always** `guessed-unverified`, whatever a checker says.

What follows:

- On the **free default path** the rate will be about **0%**. The free `basic` checker can never confirm a mailbox, and Adzuna names nobody, so the email columns are mostly `not found` (seen in the 4.5.6 simulation).
- A paid checker turns **provider or list addresses** into `verified` (or removes invalid ones). It **cannot** turn a guess into `verified`.
- So, to raise the rate: (1) get real addresses (Hunter / Apollo finder, or a `csv` finder with your own permitted list), then (2) check them with a paid checker. See 4.9.5 for when to pay.
- If a client wants only confirmed emails, set `emails.include_unverified: false`. Real demo result: `verified 5, risky 0, guessed-unverified 0, not found 5`. The rows stay (company, job, person, LinkedIn); only the unconfirmed emails are hidden.
- Tell the client in advance what the labels mean. Never promise a bounce rate.

### 4.8 Managing do-not-lists

A **do-not-list** (suppression list) holds people and companies that must never appear. There are three places:

| List | Scope | Where it lives | How to edit |
|---|---|---|---|
| **Global** | Never delivered to **any** client (and never contacted in outbound mode) | Table `suppression` in a database file: without `-p`/`--db` it is `data/leadgen.db`, which is what clients on `recruitment-delivery.yaml` use | `leadgen suppress add/remove/list VALUE` |
| **Per client** | Never delivered to **this** client | Table `client_suppression` in that client's database (found automatically from the client's playbook) | `leadgen suppress ... --client NAME` |
| **Client file `exclusions`** | This client | `clients/NAME.yaml` | Edit the file |

**Kinds** (`--kind`): `email`, `domain`, `company`, `linkedin`. Without `--kind`: a linkedin.com URL counts as `linkedin`, a value with `@` as `email`, anything else as `domain`. **Company names always need `--kind company`.** Also accepted: `Name <email>` (the email is used), `@domain` or `*@domain` (the whole domain).

How values are stored (verified): emails in lower case; domains bare (`https://www.X.com/about` -> `x.com`); LinkedIn URLs without the scheme, query or anything after `/in/<name>`, with any country host (`uk.linkedin.com`) turned into `linkedin.com`. Company names are normalised: lower case, punctuation removed, and legal suffixes stripped from the end (`inc`, `incorporated`, `llc`, `ltd`, `limited`, `plc`, `corp`, `corporation`, `co`, `company`, `gmbh`, `ag`, `sa`, `sas`, `bv`, `nv`, `pty`, `pte`, `llp`, `lp`, `group`, `holdings`, and `the`), and a leading `the` dropped too (`_COMPANY_SUFFIXES` in `leadgen/utils.py`). So `Example Corp` is stored as `example` and `Beta Holdings LLC` as `beta`. Be careful with short names: a stored `example` matches any company whose normalised name is exactly `example`.

Real commands and output. The test used a scratch database via `--db`, so the path printed in each message is shown here as the default database it would name without `--db`:

```
$ leadgen suppress add person@example-corp.example --reason "asked not to be listed"
Added 1 value(s) to the suppression list in data/leadgen.db.
$ leadgen suppress add "Example Corp" --kind company
Added 1 value(s) to the suppression list in data/leadgen.db.
$ leadgen suppress add "Example Corp"
warning: skipped 'Example Corp' - not a valid email address, domain or LinkedIn URL (for a company name add --kind company)
Added 0 value(s) (of 1 given) to the suppression list in data/leadgen.db.
error: 1 value(s) were not added - fix them and add them again (e.g. jane@acme.com or acme.com, or --kind company "Acme Corp")
$ leadgen suppress list
4 suppressed value(s) in data/leadgen.db:

value                               kind      reason                  added
----------------------------------  --------  ----------------------  -------------------
person@example-corp.example         email     asked not to be listed  2026-09-25T06:56:13
linkedin.com/in/example-person-123  linkedin  manual                  2026-09-25T06:56:13
example-corp.example                domain    manual                  2026-09-25T06:56:13
example                             company   manual                  2026-09-25T06:56:13
```

(The reason defaults to `manual`.) Per client:

```
$ leadgen suppress add bigclient.example --client demo-client --reason "their client"
Added 1 value(s) to the do-not-list of client 'demo-client' in data/demo-delivery.db.
They are never delivered to demo-client (checked before any paid lookup).
$ leadgen suppress list --client demo-client
2 value(s) on the do-not-list of client 'demo-client' in data/demo-delivery.db:
...
Also left out by the client file (clients/demo-client.yaml) 'exclusions': companies: Riverwalk Hospitality Group; domains: gulfcoastpaper-demo.com; keywords: staffing, recruiting, recruitment
$ leadgen suppress remove bigclient.example --client demo-client
Removed 1 value(s) from the do-not-list of client 'demo-client' in data/demo-delivery.db.
```

Removing a value that isn't on the list prints `Removed 0 value(s) (of 1 given)` and still exits 0.

**Bulk import:** `--file` takes a text file (one value per line) or a CSV / TSV export. Its email column is used, else its domain / website column; with `--kind company`, its company column. Verified: a CSV with an `email` column added 2 emails; a text file of two company names with `--kind company --client acme` added 2 companies.

**When the lists are applied** (verified by a delivery): companies and domains are removed **before any paid lookup**. People (email, LinkedIn) are skipped as soon as they are found, before their email is checked. Dry runs apply the lists too. In the test, adding one company to the demo client's list made `on a do-not-list` go from 3 to 5 (the company, plus a globally suppressed person), and `not_delivered.csv` said `on this client's do-not-list (company name)`.

**Watch the database.** The global list is per database file. A client on `demo-delivery.yaml` uses `data/demo-delivery.db`, so for it use `-p playbooks/demo-delivery.yaml` or `--db data/demo-delivery.db`. `--client NAME` always finds the right database by itself (unless you pass `--db`).

**Compliance habit:** an opt-out request ("please don't list me") goes on the **global** list the same day, as an email and/or LinkedIn URL, with a `--reason`. leadgen has no country-specific legal rules built in. The report contains real people's names, titles and work emails, so check the data-protection rules that apply to you, your clients and the people listed. This is not legal advice.

### 4.9 Budget and cost control

#### 4.9.1 What costs money

| Free | Paid ("paid lookup" = one request to a paid provider) |
|---|---|
| Adzuna (free key, rate-limited), Greenhouse / Lever / Ashby job boards, `csv` / `json` sources, `csv` and `pattern` finders, `basic` checker, file exporters, console | TheirStack, Apollo (source + finder), Apify, `linkedin_jobs`, Hunter (finder + verifier), MillionVerifier, ZeroBounce, NeverBounce, **AI** (Anthropic / OpenAI / compatible) |

`leadgen adapters` marks every paid type as `network (paid)`. As shipped, every paid tool in `recruitment-delivery.yaml` is `enabled: false`, so the default delivery costs nothing. **Each AI call counts as one paid lookup**: in the simulation below, 10 AI opening lines showed `paid lookups 10`. So `--budget` also caps AI lines.

#### 4.9.2 The cap: `--budget` and `max_paid_lookups`

The first one set wins:

1. `--budget N` on `leadgen deliver` or `leadgen run` (`0` = no cap at all);
2. the client file's `budget.max_paid_lookups` (`0` = no cap *of its own*; the playbook's cap still applies);
3. the playbook's `usage.max_paid_lookups` (`0` = no cap).

`--budget -1` is refused: `error: --budget must be 0 or more (got -1): it is the most paid lookups this run may make (0 = no cap). For no paid calls at all, use --dry-run.` (exit 2).

When the cap is reached, paid requests are refused **before** they reach the network. Paid sources and finders stop, the verifier falls back to the free `basic` checker, free tools keep working, and the QA summary warns. **Simulated** example (real CLI, a copy of the demo client with the MillionVerifier checker switched on through `overrides`, `usage.cost_per_call: {millionverifier: 0.004}`, and canned MillionVerifier answers reporting 9497 credits):

```
$ leadgen deliver --client budgetdemo --budget 3
Delivering the Hiring Signal Report for Budget Demo (budgetdemo) - at most 3 paid lookup(s) ...
warning: paid-lookup budget of 3 reached: no more contact lookups this run
warning: paid-lookup budget reached: remaining emails checked with the free basic checker
...
  API usage: paid lookups 3/3
    verifier millionverifier (paid): 3 calls, 9497 credits left, ~$0.0120
    estimated cost: ~$0.0120
  WARNING: Paid-lookup budget reached (3 of 3 paid lookups used): some contacts / emails were not looked up with the paid providers. Raise budget.max_paid_lookups in the client file or run with --budget N.
```

The same run without `--budget` made 5 checker calls (`paid lookups 5 (no cap)`, `~$0.0200`). The 0.004 price is the playbook's **example** placeholder, not a real quote.

#### 4.9.3 Reading the usage lines

```
API usage: paid lookups <used>/<cap>          ("<used> (no cap)" without a cap)
  <kind> <type> (paid|free): N calls[, N skipped by budget][, IN+OUT tokens][, N credits left][, ~$cost]
  estimated cost: ~$X                         (only when something can be priced)
  estimated cost: n/a (set usage.cost_per_call.<type> to your plan's price per request)
```

- `no API calls (free / offline run)` = nothing touched the network.
- **`cost_per_call`**: providers price per plan, so leadgen has no built-in per-request prices. Put **your** plan's price per request (USD) in the base playbook: `usage: {cost_per_call: {millionverifier: ..., hunter: ..., apollo: ..., theirstack: ...}}`. The playbook's comment gives placeholder examples (`apollo: 0.05, hunter: 0.03, millionverifier: 0.004, theirstack: 0.50`) marked "EXAMPLES - use your own". For TheirStack, which charges per job returned, the playbook says to use the price of one page of `limit` jobs.
- **AI prices** come from the built-in table `LLM_PRICES` in `leadgen/usage.py` (labelled "Anthropic first-party API list prices, cached 2026-06"; full table in [2.11](#211-usage-metering-the-budget-and-prices-leadgenusagepy)). Unknown models (including every OpenAI model) are priced at a deliberately high fallback of 10 / 50 USD per million input / output tokens, so cost caps err on the safe side. Set `usage.llm_price_per_mtok: {model: {input: X, output: Y}}` for other models.
- The Anthropic adapter's default model is `claude-opus-5`, used when `writer.provider: anthropic` is set without a `model`. For opening lines, set `model: claude-haiku-4-5` (the cheapest in the table).

#### 4.9.4 AI opening lines and their cap

Simulated with canned Anthropic answers (made-up token counts of 400 in / 30 out per call), `claude-haiku-4-5`:

```
  opening lines .............. 10 AI, 0 template, ~$0.0055
  API usage: paid lookups 10 (no cap)
    llm anthropic (paid): 10 calls, 4000+300 tokens, ~$0.0055
```

With `max_cost_usd: 0.003` the same run stopped after 2 AI lines:

```
  opening lines .............. 2 AI, 8 template, ~$0.0011
  WARNING: AI opening-line cost cap reached ($0.003): 8 line(s) use the free template instead. Raise opening_line.max_cost_usd in the client file to allow more AI lines.
```

Only 2 fitted in $0.003, although they cost $0.0011, because the cap is checked against a **worst-case** estimate before each call: prompt characters / 4 as input tokens, plus the full output allowance of 400 tokens (`DEFAULT_MAX_TOKENS` in `leadgen/delivery/opening.py`). Derived from the price table: the worst-case output part alone is 400 x $5 / 1M = $0.002 per line with `claude-haiku-4-5`, or 400 x $25 / 1M = $0.01 with `claude-opus-5`. The check is "real spend so far + the worst case of the next call <= cap", and each call is then charged at its real cost when the provider reports token usage. So how many lines fit under the default $0.50 cap depends on the real answers, and it can be far more than the worst case suggests. Only when a provider reports no token usage is every line charged at the worst case: then the cap allows fewer than 50 AI lines per delivery on `claude-opus-5`, and at most about 250 on `claude-haiku-4-5` (fewer once input tokens are added). Real token counts were never measured.

#### 4.9.5 When to buy email-checker (verifier) credits

A decision rule, based on how the labels work (4.7.4):

1. **Not on the pure free path.** With Adzuna plus the pattern finder there are no real addresses to confirm, and guesses can never become `verified`. A checker there only weeds out invalid guesses.
2. **Buy when both are true:** (a) you get real addresses from somewhere (Hunter / Apollo finder, TheirStack hiring teams, or a permitted contact list in a `csv` finder), **and** (b) a client pays for, or keeps asking about, confirmed emails. Another trigger: the client complains about bounces from `risky` addresses.
3. **Start small.** Buy the smallest credit pack. Set `usage.cost_per_call` to its real price per check. Run with a `--budget`. Watch `credits left` in the usage line (MillionVerifier reports it; the doctor shows quota where a provider reports it).
4. **Size the budget** from how the engine behaves (derived from the code, upper bounds, not measurements):
   - at most `max(2 x leads_per_week, 10)` companies are enriched per delivery (50 for 25 leads a week, capped at 100 by the default playbook);
   - the checker is asked about at most **3** addresses per person (`MAX_CANDIDATES_TO_VERIFY = 3` in `leadgen/pipeline.py`) and stops at the first `valid`;
   - results are cached for 30 days (`store.get_verification`, `max_age_days=30`), so the same address is not paid for twice within a month;
   - so a verifier-only setup with 25 leads a week makes at most about 50 x 3 = 150 checks per delivery, usually far fewer. `--budget 150` is a safe ceiling for that setup. At the playbook's **example** $0.004 that ceiling would be $0.60. Use your real price.
   - paid **finders** add their own requests per enriched company (at least one each time they run). Finders run in order and stop once a decision-maker with an email is found, so put the cheapest first.

#### 4.9.6 A starting plan for a USD 100-200 budget (assumptions)

These are rough suggestions to test, not researched prices. Check each provider's current price list before spending.

| Stage | Spend | What you do |
|---|---|---|
| 0. Before any client | $0 | Demo, free Adzuna key, a sample report for a made-up client in your target niche (`leadgen clients new sample-finance`) or `leadgen demo -p PLAYBOOK` (the one-pager of an earlier `leadgen run -p PLAYBOOK`; `leadgen demo` without `-p` stops with an error). |
| 1. Pilot client(s) | $0 to small | Free path: companies + jobs + links + dates. Name decision-makers with a small permitted `csv` contact list you research yourself, or a provider's free tier (the README and playbook say Hunter has a small free plan; check current pricing). Be upfront that emails are labelled honestly. |
| 2. First paying client | The client's fee pays for the tools | Switch on **one** people finder (Hunter or Apollo), then one checker. Set `cost_per_call`, a client `budget.max_paid_lookups`, and check the usage lines every week. Keep AI lines off, or on `claude-haiku-4-5` with a small `max_cost_usd`. |
| Always | | Keep paid tools switched off in any playbook a non-paying client uses. Use `--budget` for every first live run with a new paid tool (`--budget 20`, as `PLAN.md` suggests). |

### 4.10 Automation

Automate the **run**. Keep the **review and sending** manual: leadgen never sends the report, on purpose.

#### 4.10.1 Linux / macOS: a wrapper script plus cron

This script was run as written in a fresh copy of the repository (with the demo client; list your own clients instead). It backs up the databases, runs doctor and deliver per client, and records each exit code:

```bash
#!/usr/bin/env bash
# weekly.sh - back up the databases, then run each client's delivery with a log.
cd "$(dirname "$0")" || exit 2          # the repo folder: .env, clients/, data/ are found from here
mkdir -p logs backups
STAMP=$(date +%F)
for db in data/*.db; do                  # 1. back up first (no other leadgen command is running)
  [ -f "$db" ] && cp "$db" "backups/$(basename "$db" .db)-$STAMP.db"
done
for client in demo-client; do            # 2. list your clients here: for client in acme northwind; do
  log="logs/deliver-$client-$STAMP.log"
  .venv/bin/leadgen doctor  --client "$client" >> "$log" 2>&1
  .venv/bin/leadgen deliver --client "$client" >> "$log" 2>&1
  rc=$?                                  # 0 = files ready, 1 = nothing delivered / every source failed, 2 = config error
  echo "$(date '+%F %T') $client exit=$rc" >> logs/weekly.log
done
```

Save it as `weekly.sh` in the repository folder and make it executable (`chmod +x weekly.sh`). Result of two runs on the same day (real):

```
2026-09-25 07:01:18 demo-client exit=0
2026-09-25 07:01:29 demo-client exit=1
```

The second `exit=1` depends on what the database already held: that run found nothing new. Re-checked on an **empty** demo database, both runs log `exit=0`, because the second run delivers the 2 held-back leads (see 4.4.3); only a third run exits 1.

The first draft of this script logged `exit=$?` inside the `echo` line. That records the exit code of `date`, not of the delivery. Capture `rc=$?` straight after the command.

Then schedule it with cron (`crontab -e`). Not tested here, since the sandbox has no cron:

```cron
# m  h  dom mon dow  command                                   (Mondays at 06:45)
45 6 * * 1  /path/to/Lead-Gen-/weekly.sh
```

Or one line per client without the script, as in the README:

```cron
45 6 * * 1  cd /path/to/Lead-Gen- && mkdir -p logs && .venv/bin/leadgen deliver --client acme >> logs/deliver-acme.log 2>&1
```

Notes:

- `logs/` is **not** in `.gitignore`. Add it, or log outside the repository. `backups/*.db` is ignored through the `*.db` rule, but a backup inside the repository folder dies with the laptop. Also copy it somewhere else (4.11).
- Alert on the exit code: non-zero means "look before sending". For email or Slack alerts, the playbook's `notify` section supports `slack` (`SLACK_WEBHOOK_URL`) and `webhook` (`LEADGEN_WEBHOOK_URL`) notifiers. They were never called live.
- If the machine is asleep at 06:45, cron skips the run. Check the log on Monday morning.

#### 4.10.2 Windows: Task Scheduler (untested)

Create `run-weekly.bat` in the repository folder:

```bat
@echo off
rem run-weekly.bat - weekly delivery (Windows). Untested sketch.
cd /d C:\path\to\Lead-Gen-
set PYTHONUTF8=1
if not exist logs mkdir logs
if not exist backups mkdir backups
.venv\Scripts\python.exe -c "import sqlite3,datetime; s=sqlite3.connect('data/leadgen.db'); d=sqlite3.connect(f'backups/leadgen-{datetime.date.today()}.db'); s.backup(d); d.close(); s.close()"
.venv\Scripts\leadgen.exe doctor --client acme >> logs\deliver-acme.log 2>&1
.venv\Scripts\leadgen.exe deliver --client acme >> logs\deliver-acme.log 2>&1
echo %date% %time% acme exit=%errorlevel% >> logs\weekly.log
```

(The Python backup one-liner was tested on Linux. If `data/leadgen.db` doesn't exist yet it creates an empty backup, which is harmless.) Then in Task Scheduler: *Create Basic Task* -> weekly, Monday 06:45 -> *Start a program*: `C:\path\to\Lead-Gen-\run-weekly.bat`, *Start in*: `C:\path\to\Lead-Gen-`. Tick "Run whether user is logged on or not" if the laptop may be locked.

The README's direct form (no batch file): program `C:\path\to\Lead-Gen-\.venv\Scripts\leadgen.exe`, arguments `deliver --client acme`, "Start in" `C:\path\to\Lead-Gen-`. **"Start in" is essential**: without it `.env`, `clients/` and `data/` are not found.

#### 4.10.3 Cloud schedulers

If you move the weekly run to GitHub Actions or a server, the database (`data/*.db`) **must survive between runs**. Otherwise the ledger forgets what was delivered and clients get repeats. Keys then go into the host's secret store, not a committed `.env`.

### 4.11 Backing up the ledger database

**Why:** the database is the only record of what each client has received. Lose it and the next delivery can resend old companies, jobs and people. It also holds the do-not-lists (losing them is a compliance problem), the job history used to spot re-posted ads, and the 30-day email-check cache (losing that means paying for checks again).

**Which files:** each playbook's `storage.path`:

| Database | Used by |
|---|---|
| `data/leadgen.db` | `recruitment-delivery.yaml` (every client file's default base) **and** `my-agency.yaml`, and any command run without `-p`/`--db` |
| `data/demo-delivery.db` | `demo-delivery.yaml` / `demo-client` |
| `data/demo.db` | `demo-offline.yaml` (outbound demo) |
| anything in a client's `overrides: {storage: {path: ...}}` | that client |

Tables (verified in the demo database): `deliveries`, `delivery_runs`, `client_suppression` (the ledger and client do-not-lists), plus `suppression`, `runs`, `leads`, `lead_runs`, `events`, `signal_history`, `verifications`, `replies`, `followups`, `sqlite_sequence`.

**How.** leadgen uses SQLite in its default mode (no WAL, the "write-ahead log" mode that keeps extra files next to the database). So copying the `.db` file while **no leadgen command is running** gives a complete backup. The safest method, which also works while something is running, is SQLite's backup function (tested):

```bash
python -c "import sqlite3,datetime; s=sqlite3.connect('data/leadgen.db'); d=sqlite3.connect(f'backups/leadgen-{datetime.date.today()}.db'); s.backup(d); d.close(); s.close()"
```

**Check a backup** (tested):

```bash
python -c "import sqlite3; print(sqlite3.connect('backups/leadgen-2026-09-25.db').execute('PRAGMA integrity_check').fetchone()[0])"   # -> ok
leadgen clients --db backups/leadgen-2026-09-25.db     # shows each client's deliveries from the backup
```

Verified: `leadgen clients --db <backup>` showed `demo-client ... 2  2026-09-25  12`, the same history as the original.

**Restore:** stop any scheduled run, copy the backup over `data/leadgen.db` (keep the broken file under another name), run `leadgen clients` to confirm the history, then continue. Anything delivered **after** that backup is no longer in the ledger. Re-add it by hand if you can: put the companies from those deliveries on the client's do-not-list with `leadgen suppress add --file <that delivery's CSV> --kind company --client NAME`. Verified: feeding the demo's first delivery CSV to that command added its 10 companies (from the `Company` column), and the next demo delivery then held only the 2 companies not yet sent. This protects companies only. Jobs and people from the lost deliveries are not re-recorded.

**Routine:** back up before every weekly run (the script in 4.10 does). Keep a copy **off the machine** (an encrypted cloud folder or USB drive), because the backup contains personal data. Keep a few weeks of copies and delete older ones.

### 4.12 Command reference

Verified against `leadgen --help` and every `leadgen <command> --help`.

#### 4.12.1 Global options

They work before or after the command (`leadgen --db X suppress list` and `leadgen suppress list --db X` both work).

| Option | Meaning |
|---|---|
| `-h`, `--help` | Help for leadgen or a command |
| `--version` | `leadgen 0.1.0` (top level only) |
| `-p PATH`, `--playbook PATH` | The playbook YAML file. Ignored by `deliver` (with a warning). |
| `--db PATH` | This SQLite database instead of the playbook's `storage.path` |
| `-v`, `--verbose` | More output: `-v` info, `-vv` debug, plus full tracebacks on errors |
| `--env-file PATH` | KEY=VALUE file with API keys (default `.env`; variables already set in the shell win). A named file that doesn't exist is an error. |

`leadgen` with no command prints the help and exits 2.

#### 4.12.2 Delivery commands (the product)

| Command | Arguments and flags | What it does | Exit codes |
|---|---|---|---|
| `leadgen deliver` | `--client NAME` (required; `clients/NAME.yaml`, or `NAME.yaml`, or a path), `--clients-dir DIR` (default `clients`), `--dry-run` (PREVIEW files, offline sources only, nothing recorded, no Sheets push, no paid calls), `--budget N` (max paid lookups; 0 = no cap; overrides client and playbook), `--out DIR` (default: the client's `delivery.folder`; `{client}` and `{date}` are filled in) | One client's Hiring Signal Report: files + QA summary + ledger record | 0 delivered; 1 nothing delivered or every source failed; 2 command / client-file error |
| `leadgen clients [list]` | `--clients-dir DIR` | Every client: display name, leads/week, deliveries, last delivery, total delivered (distinct companies). Invalid client files are listed as `(invalid client file - see the error below)` with the errors. With `--db`, history is read from that database. | 0; 1 if any client file is invalid |
| `leadgen clients new NAME` | `--clients-dir DIR` | Copy `clients/_template.yaml` to `clients/NAME.yaml` (never overwrites) | 0; 1 file exists; 2 bad name |
| `leadgen doctor` | `-p PLAYBOOK` **or** `--client NAME` (exactly one), `--clients-dir DIR`, `--dry-run` (only checks keys are set) | One free live check per key (never a paid lookup, never counted against the budget) | 0 all ok / skipped; 1 any FAILED / MISSING KEY; 2 neither or both of `-p` / `--client` |
| `leadgen suppress add\|remove\|list [VALUE]` | `--kind {email,domain,company,linkedin}`, `--client NAME`, `--clients-dir DIR`, `--reason REASON`, `--file CSV/TXT` | The global do-not-list, or with `--client` that client's list (4.8) | 0; 1 some values not added; 2 no value / file given |
| `leadgen run` | `-p` required; `--out DIR` (default `output/<playbook>/`, one sub-folder per run), `--limit N` (only enrich / score the top N companies), `--dry-run`, `--budget N` | One pipeline run **without** a client or ledger. Review files: `opportunities.csv`, `rejected.csv`, `summary.json` (outbound playbooks also write upload files). | 0; 1 only if every source failed; 2 no playbook / config error |
| `leadgen validate` | `-p` required; `--dry-run` (network adapters' keys become optional) | Offline checklist: mode, adapters, config, which keys are set | 0 ok; 1 problems; 2 no playbook |
| `leadgen adapters` | none | Every adapter type per kind: offline / network / network (paid), its key, notes (use-at-own-risk, outbound only) | 0 |
| `leadgen init NAME` | `--template {generic,recruitment,saas-funding,local-business,agency-outreach}` (default `generic`), `--dir DIR` (default `playbooks`) | A new playbook copied from `playbooks/templates/` (never overwrites) | 0; 1 file exists |

#### 4.12.3 Reports

| Command | Arguments and flags | What it does | Exit codes |
|---|---|---|---|
| `leadgen demo` | `--run RUN_ID` (`latest`, the default), `--top N` (default 5; 0 = all), `--prospect NAME` (title + file name), `--no-mask` (show emails in full; masked by default), `--out DIR` (default: the run's folder) | A "live opportunities" one-pager (Markdown + HTML) from a `leadgen run`, e.g. to show a prospect. Verified: wrote `demo-acme-staffing.md` and `.html`. | 0; 1 no finished run |
| `leadgen leads` | `--run RUN_ID` (`latest` default, or `all`), `--tier {hot,normal,skip}`, `--limit N` (default 20; 0 = all) | The leads of a run as a table (id, score, tier, stage, company, contact, email [status], top signal) | 0; 1 no finished run |
| `leadgen stats` | `--since YYYY-MM-DD`, `--runs N` (default 10) | Funnel by stage, key rates, reply categories, tiers, recent runs. Without `-p`: all playbooks in `data/leadgen.db`. | 0 |
| `leadgen mark` | `--email EMAIL` **or** `--lead ID` (one required), `--stage {sourced,qualified,enriched,verified,ready,exported,replied,positive,booked,won,lost}` (required), `--note NOTE`, `--force` (allow moving backwards) | Move a lead to a stage by hand. Stages only move forward without `--force` (`lost` is always allowed). Not limited to outbound mode (verified on a delivery-mode database). | 0; 1 no such lead |

Note: runs made by `leadgen deliver` are stored under the playbook name `client-<name>`, so `leads` / `demo -p <base playbook>` don't show them. A delivery's full lead list is in its `_internal/<run id>/opportunities.csv`.

#### 4.12.4 Outbound-only commands

These refuse to run on a delivery-mode playbook (4.13).

| Command | Arguments and flags | What it does |
|---|---|---|
| `leadgen replies` | `--file CSV` (required: replies export with from / subject / body columns ...), `--out DIR` (default `output/<playbook>/`) | Sort each reply (positive, question, referral, timing, ooo, negative, unsubscribe, bounce, other), act on it, write `replies_classified.csv` |
| `leadgen serve` | `--host HOST` (default `127.0.0.1`), `--port PORT` (default `8787`), `--token TOKEN` (default `$LEADGEN_WEBHOOK_TOKEN`) | Reply webhook server for Instantly / Smartlead / generic payloads |
| `leadgen followups` | `--done ID` (mark one done), `--days N` (also show those due in the next N days; default 0 = today) | Follow-ups created by timing / out-of-office replies |

Exit codes everywhere: `0` ok, `1` the command ran but found a problem, `2` usage or configuration error (one `error:` line on stderr; `-v` shows the traceback), `130` interrupted with Ctrl+C.

### 4.13 Outbound mode operations (brief)

Outbound mode is kept working but is off by default. You use it only to find clients for **your own** business. It never touches client deliveries.

**The gate** (real output):

```
$ leadgen followups
error: leadgen followups is an outbound-mode feature, and this playbook runs in delivery mode (the default). Add 'mode: outbound' to the playbook to use it.
No playbook was given (-p), so the default mode (delivery) applies. Name an outbound-mode playbook, e.g. leadgen followups -p playbooks/demo-offline.yaml
```

`replies` and `serve` give the same kind of message (e.g. `error: leadgen serve (the reply webhook server) is an outbound-mode feature, and playbook 'recruitment-delivery' runs in delivery mode (the default). ...`). Exit 2.

**`playbooks/my-agency.yaml`** (mode `outbound`) looks for B2B service firms (recruitment, marketing, IT services, consultancies) hiring sales / business-development staff, and targets their founder or MD. As shipped it uses **paid** tools only: TheirStack source, Apollo + Hunter finders (+ free pattern), MillionVerifier checker, AI writer `anthropic` / `claude-opus-5`. `leadgen validate -p playbooks/my-agency.yaml` without keys shows 5 `[FAIL]` lines. It has no paid-lookup cap of its own, so always pass `--budget`. It shares `data/leadgen.db` with `recruitment-delivery.yaml`, so the global do-not-list is shared. Its `offer` section and its `writer.extra_instructions` still pitch the old service ("outbound lead generation and AI automation", and a new sales hire starting with "meetings already booked"): rewrite both to pitch the Hiring Signal Report before any real use (suggested edits: [5.9.3](#593-using-playbooksmy-agencyyaml-optional-needs-edits-first)).

Operating steps:

1. `.env`: `SENDER_NAME`, `SENDER_TITLE`, `SENDER_COMPANY`, `SENDER_WEBSITE`, `BOOKING_LINK`, plus the keys it uses.
2. `leadgen validate -p playbooks/my-agency.yaml` and `leadgen doctor -p playbooks/my-agency.yaml`.
3. `leadgen run -p playbooks/my-agency.yaml --dry-run`. Verified: it finds nothing (all its sources are network sources) and writes `instantly_upload.dry-run.csv`, labelled "dry-run rehearsal only - nobody was recorded as handed over: do NOT import it".
4. A small paid test: `leadgen run -p playbooks/my-agency.yaml --limit 10 --budget 100`.
5. Review `opportunities.csv`, then import `instantly_upload.csv` into your Instantly campaign. Writing an upload file counts as handing those people over: nobody is handed over twice by the same playbook.

The offline outbound demo (`leadgen run -p playbooks/demo-offline.yaml`, database `data/demo.db`) was run here: 18 companies found, 7 sequences written, 7 handed over, upload files `instantly_upload.csv` / `smartlead_upload.csv`.

**Replies CSV** (verified with `examples/data/demo_replies.csv`): `leadgen replies -p <outbound playbook> --file replies.csv`. Columns are matched by name: sender (`from`, `from_email`, `email`), `subject`, text (`body`, `text`, `message`), `received_at`, optional `lead_id`. Result: `By category: positive 3, referral 1, timing 1, question 1, negative 1, unsubscribe 1, ooo 1, bounce 1, other 2`. Leads were moved to positive / replied / lost; the unsubscribe, the "not interested" and the bounce were put on the do-not-list; follow-ups were scheduled; `replies_classified.csv` was written. Importing the same file twice is safe.

**Follow-ups and stages** (verified): `leadgen followups -p ... --days 30` lists what is due, `--done 1` ticks one off, and `leadgen mark -p ... --email X --stage booked --note "call booked"` moves a lead (`positive -> booked`). Moving it back without `--force` prints `stays 'booked' - stages only move forward; add --force ...`.

**Webhook server** (verified locally): `leadgen serve -p <outbound playbook> --port 8787 --token <secret>` listens on `127.0.0.1` only, with `GET /health` (answered `{"ok": true, "playbook": "demo-offline"}`) and `POST /webhook`, `/webhook/reply`, `/webhook/instantly`, `/webhook/smartlead`. With a token, a request without it got **401**. Without a token it starts with `auth: none - set --token or $LEADGEN_WEBHOOK_TOKEN before exposing this server`. Make a token with `python3 -c "import secrets; print(secrets.token_hex(16))"`, put it in `.env`, and put the server behind HTTPS (a reverse proxy or tunnel) before giving its URL to Instantly / Smartlead. Never tested with the real tools.

### 4.14 Troubleshooting

Messages marked "real" were triggered in this review. Others are quoted from the README / code and labelled.

| Symptom / message | Cause | Fix |
|---|---|---|
| `leadgen: command not found` (README) | The virtual environment is not active, or the package is not installed | `source .venv/bin/activate` (Windows `.venv\Scripts\activate`), or `pip install -e .` |
| pip says "externally-managed-environment" (README) | Installing into the system Python | Use a virtual environment (4.2) |
| `error: env file not found: <path>` (real, exit 2) | `--env-file` names a file that doesn't exist | Fix the path. Without `--env-file`, a missing `.env` is simply skipped. |
| `MISSING KEY  source adzuna  missing key - set $ADZUNA_APP_ID and $ADZUNA_APP_KEY ...` (real, doctor exit 1) / `[FAIL] source adzuna - missing credential: set $ADZUNA_APP_ID ...` (real, validate exit 1) | Key not set, or set to an empty value | Add both values to `.env` in the folder you run from. `leadgen doctor --client NAME` again. |
| `error: source adzuna: adzuna: missing credential (set $ADZUNA_APP_ID)` then `WARNING: Source problem - ...` and `error: every source failed (1 of 1) - see the warnings above` (real, deliver exit 1) | A real delivery without keys | Same fix. Don't send the empty files. |
| `FAILED  source adzuna  could not reach api.adzuna.com (ProxyError) - check your internet connection / proxy` (real, after 3 retries) | No internet / a proxy blocking the call | Check your connection. (This is what the sandbox produced.) |
| Doctor says `failed ... key rejected` (README) | Wrong, expired or under-permissioned key | Re-copy the key from the provider's dashboard |
| `error: client 'acm' not found: there is no clients/acm.yaml. Did you mean 'acme'? Clients in clients/: acme. Create it with: leadgen clients new acm` (real, exit 2) | Typo in the name, or running from the wrong folder | Run from the repository folder, check `leadgen clients`, or pass `--clients-dir` |
| `error: clients/typo.yaml: unknown setting 'leads_per_wek' - did you mean 'leads_per_week'?` (real, exit 2) | A misspelt key in the client file | Fix the key named in the message |
| `error: clients/typo.yaml has 2 problems:` followed by `- unknown setting ...` and `- freshness_days must be a whole number > 0 (got 'seven')` (real, exit 2) | Several mistakes; all are listed at once | Fix each line |
| `error: <client file>: base playbook 'playbooks/recruitment-delivry.yaml' not found (looked for: ...). Fix the 'playbook:' line in the client file.` (real, exit 2) | Wrong `playbook:` path in a client file | Fix the path. It is looked up from the current folder, next to the client file, then one folder up. |
| `error: playbook not found: playbooks/nope.yaml` (real, exit 2) | Wrong `-p` path | Fix the path (`ls playbooks`) |
| `error: 'run' needs a playbook: add -p playbooks/<name>.yaml ...` (real, exit 2); the same for `validate` | Missing `-p` | Add `-p` |
| `error: doctor needs -p PLAYBOOK or --client NAME ...` / `error: doctor checks one thing at a time: give -p PLAYBOOK or --client NAME, not both` (real, exit 2) | Doctor needs exactly one target | Give one |
| `error: leadgen followups is an outbound-mode feature, and this playbook runs in delivery mode (the default). ...` (real, exit 2); same for `replies` / `serve` | Outbound command on a delivery-mode playbook, or without `-p` | Add `-p` with a `mode: outbound` playbook (`playbooks/demo-offline.yaml`, `playbooks/my-agency.yaml`) |
| `leadgen deliver: error: the following arguments are required: --client` (real, exit 2) | Missing `--client` | Add it |
| `error: --budget must be 0 or more (got -1) ...` (real, exit 2) | Negative budget | Use 0 (no cap) or a positive number; `--dry-run` for no paid calls at all |
| `warning: -p is ignored by 'deliver': the client file's 'playbook:' line names the base playbook` (real) | `-p` passed to `deliver` | Change the client file's `playbook:` line instead |
| `Note: nothing was found because every source uses the network and --dry-run skips them. ...` (real, exit 1) | Dry run on a network-only playbook (the default one) | Expected. Rehearse on a database copy (4.5.5) or run for real. |
| `WARNING: Low volume: ...` / `Nothing to send this time: no new leads for ...` + `error: no leads were delivered ...` (real, exit 1) | Criteria too narrow, niche used up, or sources thin | 4.7.3. Read `_internal/not_delivered.csv`. |
| Every Adzuna company rejected with `location not in ICP [Texas] (got: <city>, <county>; US; ...)` (simulated only) | Adzuna's location text may not name the state | See 4.5.3 (cities / counties, `where`, or `mapping: {location: location.area}`). Check on your first live run. |
| Decision-maker and email columns empty (`not found`) | The free setup can't name people (Adzuna has no people) | Hunter / Apollo / TheirStack, or a `csv` finder with a permitted contact list (4.5.6, 4.9.5) |
| Few `verified` emails | The free `basic` checker can't confirm mailboxes; guesses are never `verified` | 4.7.4 |
| `WARNING: Paid-lookup budget reached (...)` (simulated) | The cap was hit | Raise `budget.max_paid_lookups` or `--budget`, if worth it |
| `WARNING: AI opening-line cost cap reached ($X) ...` (simulated) | `max_cost_usd` too low for the model / volume | Raise it, switch to `claude-haiku-4-5`, or accept template lines |
| `warning: skipped 'Example Corp' - not a valid email address, domain or LinkedIn URL (for a company name add --kind company)` (real, exit 1) | A company name without `--kind company` | Add `--kind company` |
| `error: suppress add: give a VALUE or --file` (real, exit 2) | Nothing to add | Give a value or `--file` |
| `leadgen suppress` didn't affect a delivery | The global list is per database file | Add `-p <that client's playbook>` / `--db`, or use `--client NAME` |
| `Careful: this delivery used --db ..., not ... (the database of this client's playbook ...)` (real, printed after a real delivery with leads) | `--db` points to another database (e.g. a rehearsal) | Send these files only if that database is where you keep every delivery to this client, never a rehearsal's |
| Files went to `<date>-2` (real `note:` line) | A delivery for that client and date already existed; nothing was overwritten | Normal. Send the new folder if it has leads. |
| A client received a company twice | Different database files (`--db`, clients on playbooks with different `storage.path`, a lost or restored database), a renamed client file, or `redelivery_days` / `dedupe` allow it | Keep one database per client and back it up (4.11). Never rename client files. |
| ``error: no finished runs for playbook 'demo-delivery' in <db> yet - run `leadgen run -p playbooks/demo-delivery.yaml` first`` (real, exit 1) | `leads` / `demo` on a database without runs of that playbook | Run the playbook first. For a delivery, read `_internal/<run>/opportunities.csv`. |
| `error: no lead with email ... in playbook ... found in <db>` (real, exit 1) | `mark` with an unknown email | Check the email / use `--lead ID` from `leadgen leads` |
| `error: unexpected UnicodeEncodeError: ...` (real when output encoding was forced to ASCII) | The console / log encoding can't show a character | Set `PYTHONUTF8=1` (4.2.2, 4.10.2) |
| `error: <path> already exists - edit that file, or choose another name` (real, `clients new`, exit 1); `... already exists - choose another name or delete that file first` (real, `init`, exit 1) | Never overwrites | Edit the existing file or pick another name |
| Need more detail on any error | | Add `-v` (info) or `-vv` (debug + full traceback) |

### 4.15 Operations today, and what will need to change

| Area | Today | Change needed in future | When (trigger) |
|---|---|---|---|
| Live providers | Every connector built from provider documentation and tested only with canned answers | First live week per provider: `leadgen doctor`, then a small real delivery with `--budget 20`, then fix whatever the real service does differently (e.g. Adzuna location format, 4.5.3) | Before the first paying delivery on that provider |
| Source search words | Adzuna `queries` / TheirStack `job_titles` live in the base playbook, not the client file | One playbook copy per niche now. Later: have each client's `roles` drive the search words. | A second niche; more than a handful of clients |
| Scheduling | By hand, or cron / Task Scheduler on your own machine | A small always-on server or scheduled cloud job, with the database kept between runs, secrets in the host's secret store, alerts on non-zero exit codes | Once missed Mondays start to happen, or more than a few clients |
| Backups | Manual or script copies of `data/*.db`; `logs/` not git-ignored | Automatic dated backups with an off-machine copy; add `logs/` to `.gitignore` | Now (first real client) |
| Delivery log / billing | Your own spreadsheet; `leadgen clients` totals | A proper client log (delivered, sent, paid). Billing and a client portal are not built. | Several paying clients |
| Cost tracking | Usage lines per run; `cost_per_call` must be filled in by hand | Fill `cost_per_call` from real invoices; review cost per delivery vs price monthly; keep `LLM_PRICES` in `leadgen/usage.py` current | As soon as any paid tool is switched on |
| Email quality | Free path: mostly `not found` / `guessed-unverified` | One people finder + one checker, with budgets | First client who pays for names / verified emails |
| Encoding on Windows | No forced UTF-8 output | Set `PYTHONUTF8=1`, or make the CLI write UTF-8 itself | If you run on Windows |
| Documentation | README / template say a client can't widen source windows; the code does (4.5.2) | Fix the docs (or the code) so they agree | Next documentation pass |
| Privacy operations | Do-not-lists; nothing deletes old personal data | A purge / retention routine for old deliveries and database rows; a written opt-out process | Before selling in regulated markets (e.g. UK / EU) |
| Outbound (own prospecting) | `my-agency.yaml` uses paid tools only, no budget cap of its own; offer text not yet rewritten | Rewrite `offer`; always use `--budget`; start from a CSV list you research yourself to stay cheap | When you start prospecting agencies |

### 4.16 What was verified, and what was not

**Verified by running in the sandbox:** installation in a fresh copy (`pip install -e .`, `.[dev]`, `.[sheets]`); the test suite (2073 passed); every command's `--help`; the demo client (preview, three real deliveries, folder layout, CSV contents, `clients` listing); `clients new` and its errors; `doctor` (missing key, dry run, network failure, Google Sheet row); `validate`; the dry-run note; every error message marked "real" in 4.14; `suppress` in all forms (global, per client, file import, value normalisation); do-not-lists removing leads; `emails.include_unverified: false`; `run`, `leads`, `stats`, `demo`, `init`, `adapters`, `mark`; the outbound demo, `replies`, `followups`, `serve` (health, 401 without a token, `auth: none`); the weekly wrapper script; database backup, integrity check and reading history from a backup; the client-file source-window widening.

**Simulated only (real CLI, canned web answers):** the budget cap with a paid checker, credits-left and cost lines; AI opening lines and their cost cap (token counts made up); the default Adzuna delivery path and the location-matching problem.

**Not verified:** any live provider call; the real Adzuna location format; a successful `doctor` `ok` row; Google Sheets pushes; Slack / webhook notifiers; Windows steps (venv activation, PowerShell policy, Task Scheduler, the `.bat` file); cron scheduling itself; provider prices (none were researched: all dollar figures are the playbook's placeholder examples or derived from `LLM_PRICES`); the time estimates in 4.6.

---

## 5. Business and agency plan

This section is the owner's money-making playbook. The goal is to get the first paying
client as fast as possible on a starting budget of **USD 100-200**, using the `leadgen`
engine as it exists today. It also tells a future developer or AI assistant which parts of
the plan the code already supports and what would have to change later (see
[5.19](#519-current-stack-vs-business-needs-what-exists-today-and-what-each-will-need-later)).

**How to read the numbers in this section.** Every number carries one of the labels explained in [How to use this document](#how-to-use-this-document): **engine fact**, **assumption**, **suggestion**, **target** or **check current pricing**.

One more thing that is true everywhere in this section: **no data provider has ever been
called live from this codebase.** Outbound network access was blocked in the build
environment. Every connector (Adzuna, TheirStack, Apollo, Hunter, the email checkers, Google
Sheets, the AI providers) was built from the provider's documentation and tested against
canned sample responses (about 2,073 automated tests, all offline). Before you sell anything
that depends on a provider, run `leadgen doctor` and a small real delivery with `--budget`
(see [5.11](#511-onboarding-checklist-for-a-new-client)).

### 5.1 The business in one paragraph

You sell a weekly **Hiring Signal Report** to small, specialised recruitment and staffing
agencies. Each report is a lead file: companies that posted a job **in the last 7 days**
(engine fact: `freshness_days` defaults to 7) for the roles that agency fills, in the
region it works in, with the job link and date, a 0-100 score (hot leads first), and, where
it can be found, the person most likely to own the hire and their email with an **honest
label**. A client never receives the same company, job or person twice (engine fact: the
ledger in `leadgen/delivery/ledger.py`).

**What you do not do.** You do not email, call or message anyone for your clients. The
agency does its own outreach. The engine enforces this: `mode: delivery` (the default,
`leadgen/modes.py`) stops after scoring and writing files. The old outreach engine
(`mode: outbound`) is kept, switched off by default, and is only for finding **your own**
clients (`playbooks/my-agency.yaml`).

**Why this is a good first business for a student with USD 100-200:**

- The default setup costs nothing to run in API fees (engine fact: none of its parts counts
  as a paid lookup - Adzuna, the job boards, CSV files, the `pattern` email guesser and the
  `basic` email checker; that Adzuna's key is free comes from the README and playbook
  comments, not a live check, so confirm Adzuna's current terms).
- You can show a prospect a real sample of the product before they pay, for free, in about
  half an hour (assumption about your time).
- The product is a file. There is no software to host, no login, no support desk.
- Payment can be taken upfront, so you never spend money you have not received.

### 5.2 The problem chain: why an agency would pay

This is the reasoning behind the product. It is how contingency recruitment commonly works,
explained in plain terms; it is not the result of market research.

1. **Agencies earn per placement.** A *contingency* agency is paid only when a candidate
   it introduced is hired (a *placement*). The fee is commonly quoted as roughly **15-25%
   of the new hire's first-year salary** (a common range, not researched data; it varies by
   country, niche and seniority). No placement, no fee. (A *retained* agency is paid part of
   the fee upfront; those are usually senior-level search firms and are not our first target.)
2. **The bottleneck is job orders, not candidates.** A *job order* is a vacancy a company
   has asked the agency to fill. Most specialised agencies know where to find candidates in
   their niche. What limits their income is how many live vacancies they are working on.
   More job orders means more chances to place.
3. **Finding job orders is slow, manual business development (BD).** Someone at the agency
   (often the owner) scans job boards and LinkedIn, works out which companies are hiring for
   roles they fill, finds who owns the hire, and calls or emails them. It is repetitive, it
   eats hours every week, and it competes with the work that actually earns fees
   (interviewing candidates, closing placements).
4. **Timing matters.** A company that posted a job yesterday has a live, painful need right
   now. The first agency to call with relevant candidates has the edge. A few weeks later
   the role is often filled, or the company has already picked its agencies. So a list of
   **this week's** postings is worth much more than a list of companies in general.

**Our product removes step 3 and wins step 4:** every week, a short, ranked list of fresh
job postings in the agency's niche and region, with the ones they already know (their
clients, their competitors, what we sent before) taken out.

### 5.3 Ideal customer profile (ICP)

An *ideal customer profile* is a precise description of the client you want, so you spend
your limited time on the prospects most likely to buy.

#### 5.3.1 Who the clients are

| Attribute | Ideal client |
|---|---|
| **Type** | Independent recruitment or staffing agency, mostly contingency fees. |
| **Size** | 1-20 recruiters (suggestion). Small enough that the owner still does BD personally, big enough to act on 10-25 leads a week. |
| **Focus** | **One niche** (for example finance and accounting, nursing, IT, engineering, logistics, legal) and **one region** (a city, a state or one country). |
| **Evidence they fit** | Their website says "we specialise in X in Y"; they post their clients' jobs on job boards (so they are active); they are hiring a recruiter or a BD person themselves (they are growing and need more job orders). |
| **Decision-maker** | The **owner / founder / managing director**, or the **BD lead** (head of business development) in slightly larger agencies. One person decides; no procurement department. |
| **Buying power** | Can pay a few hundred USD a month from a card or bank transfer without a committee (assumption). |
| **Market** | A country your sources cover. Adzuna searches one country code at a time (engine fact: `countries:` is required, e.g. `[us]`, `[gb]`). Check the country is supported before you pitch. |

#### 5.3.2 Who they are NOT (do not chase these first)

| Not a fit | Why |
|---|---|
| Large national or global staffing firms | They have research teams, data tools and slow buying processes. |
| In-house talent teams at employers | They fill their own roles; they do not need other companies' job orders. |
| Retained executive-search firms for C-level roles | Those roles are often not advertised publicly, so job postings are a weak signal for them. |
| Generalist agencies with no niche | Their "roles" list is everything, so a weekly file is either huge or unfocused. Possible later, not first. |
| Companies that want you to **do the outreach** for them | That is not the product (delivery mode never sends anything). Say no politely. |
| Job seekers, candidates, or anyone who wants a CV database | Not the product. |
| Agencies in a niche or country your sources don't cover | You would sell something you cannot deliver. Run a sample first; if it is thin, walk away. |

#### 5.3.3 Qualifying questions (ask before you offer a pilot)

1. Which roles do you place most often, and where?
2. How do you find new client companies today, and how many hours a week does it take?
3. When a company posts a job you could fill, how quickly do you usually hear about it?
4. Roughly what is a typical fee for you on one placement? (Let them say the number; you
   will use it in the value conversation.)
5. Who are your current clients and your main competitors? (You will exclude them.)

### 5.4 What the client receives

Every delivery (engine fact: `leadgen deliver --client NAME`) writes three files named
`<client>-hiring-signals-<YYYY-MM-DD>` into `deliveries/<client>/<date>/`: a CSV (opens in Excel / Google Sheets and imports into any CRM), an Excel workbook (a **Leads** sheet, plus an **About** sheet with counts, the email-label legend and a column guide) and a branded one-page HTML summary. The HTML shows the decision-maker's name and email **status**, but not the email itself ("Emails and LinkedIn profiles are in the Excel / CSV file"), which makes it a natural free sample. The files, the columns (18, plus a 19th "Suggested opening line" only when `opening_line.enabled: true`, as in the demo; off by default for a new client) and the four email labels (`verified`, `risky`, `guessed-unverified`, `not found`) are shown with a real demo delivery in [1.3](#13-what-a-client-receives-a-walk-through-the-real-demo-output): columns in [1.3.1](#131-the-csv-the-main-product), the label wording the client sees in [1.3.2](#132-the-excel-workbook-xlsx), the HTML in [1.3.3](#133-the-html-summary-html).

#### 5.4.1 Free setup vs. enriched setup: be honest about the difference

This matters for pricing. On the free default setup, **Adzuna returns the company, the job,
its link, its date and a location, but no people and no website** (engine fact, README and
`playbooks/recruitment-delivery.yaml`). So:

| | Free setup (default) | Enriched setup (paid tools switched on) |
|---|---|---|
| Sources | Adzuna (free key), optional Greenhouse / Lever / Ashby watchlists, your own CSV | Same, plus optionally TheirStack (paid) |
| Decision-maker column | Mostly empty (`not found`), unless you add a contact list you are allowed to use (`csv` finder) | Filled by Hunter or Apollo where they know the person |
| Emails | Mostly `not found`; guesses (`guessed-unverified`) only when a name **and** a domain are known | Real addresses (`verified` / `risky`) from the provider, checked by a paid verifier |
| Cost per delivery | USD 0 in API fees (engine fact: 0 paid lookups) | A few USD per delivery at example prices, plus the provider's plan fee (see 5.7) |
| What you are really selling | A clean, fresh, de-duplicated **hiring-signal list** for their niche and region | The same list **plus** who to contact and how |

**Important detail about "verified":** a paid email checker alone does not create
`verified` emails on the free path. Pattern guesses stay `guessed-unverified` whatever a
checker says (engine fact, `email_label` in `rows.py`). `verified` only comes from a
provider-supplied address that a checker (or the provider) confirmed. So the "verified-email
upgrade" in the offer ladder means **a finder (Hunter or Apollo) plus a paid verifier**, not
a verifier alone.

### 5.5 What the problem is worth (value maths)

The point of this section is to price from the client's value, not from your costs. All
inputs below are **assumptions** unless marked otherwise.

#### 5.5.1 One placement

A placement fee = first-year salary x fee percentage. Using the common 15-25% range
(assumption, not researched data) and three example salaries (assumption):

| First-year salary (USD) | Fee at 15% | Fee at 20% | Fee at 25% |
|---|---|---|---|
| 40,000 | 6,000 | 8,000 | 10,000 |
| 60,000 | 9,000 | 12,000 | 15,000 |
| 90,000 | 13,500 | 18,000 | 22,500 |

Compared with one year of a Starter subscription at the **suggested** USD 300/month
(USD 3,600/year), one placement pays for the subscription this many times:

| Salary | 15% | 20% | 25% |
|---|---|---|---|
| 40,000 | 1.7x | 2.2x | 2.8x |
| 60,000 | 2.5x | 3.3x | 4.2x |
| 90,000 | 3.8x | 5.0x | 6.3x |

**The core sales argument:** in every cell, **one extra placement a year** pays for the
report more than once. The client does not need to believe in dozens of wins; one is enough.

#### 5.5.2 Time saved (a secondary argument)

| Input | Value | Label |
|---|---|---|
| Hours a week spent finding which companies are hiring | 3-5 | Assumption |
| Hours the report saves | 2-4 per week | Assumption |
| Value of an hour of the owner's / recruiter's time | USD 30-50 | Assumption |
| Value of time saved | USD 60-200 per week, about **USD 260-870 per month** (x 4.33 weeks) | Calculated from the assumptions |

Honest reading: at the low end, time saved alone (about USD 260/month) is a little **below**
a USD 300 Starter price. Lead with placements; use time saved as a supporting point.

#### 5.5.3 Illustrative lead funnel (every percentage is a guess)

What 25 leads a week (the engine's default `leads_per_week`) might turn into. About 108
leads a month (25 x 52 / 12).

| Step | Cautious (assumption) | Hopeful (assumption) |
|---|---|---|
| Leads the agency actually contacts | 30% -> 32 | 60% -> 65 |
| Contacts that become a real conversation | 5% -> 1.6 | 10% -> 6.5 |
| Conversations that become a job order | 20% -> 0.3 | 25% -> 1.6 |
| Job orders the agency fills | 25% -> 0.08 a month | 35% -> 0.57 a month |
| **Placements per year** | **about 1** | **about 7** |

Even the cautious column lands at about one placement a year, which by the tables above would
already cover a Starter subscription. But every input is a guess: a real client may make no
placement from the report in a year. After two months with real clients, **replace these guesses with what
your clients report** (see KPIs in 5.15).

### 5.6 The offer ladder and suggested prices

All prices below are **suggestions** to test and adjust, not researched market prices. They
are set so that a year of Starter (USD 3,000-5,400) stays under the value of one placement at
the lowest fee in 5.5 (USD 6,000), and so that you are paid before you spend anything. A year
of Growth (USD 6,000-10,800) needs about one placement at a higher fee (roughly the 60,000 or
90,000 rows of 5.5.1) to pay back.

| Step | What's in it | Suggested price | Why this step exists |
|---|---|---|---|
| **1. Free sample** | One HTML one-pager (top 10 leads) for their niche and region, made with a real delivery. Full CSV on request. | USD 0 | Proves you can find their kind of leads this week. Costs you time only. |
| **2. Paid 4-week pilot** | 4 weekly reports, 10-15 leads a week (`leads_per_week: 15`), one niche, one region, free setup plus your hand-checks. Paid upfront. | USD 99-199 for the 4 weeks | Fast first money, low risk for the client, and it filters out people who will never pay. |
| **3. Monthly subscription: Starter** | About 25 leads a week (the default), one niche, one region, CSV + Excel + HTML. | USD 250-450 / month | The core product. |
| **4. Monthly subscription: Growth** | Up to about 50 leads a week, several niches or regions, decision-makers and emails via Hunter / Apollo plus a paid verifier, opening lines, Google Sheet. | USD 500-900 / month | Covers the paid tools with margin. |

**Note on the pilot.** `PLAN.md` section 2 suggests a pilot that is "free or a small fee" (2-4 weekly reports). This section suggests a small **paid** pilot, so that you are paid before you spend anything. Both are rough ideas to test, not researched prices.

**Add-ons** (each a suggestion):

| Add-on | What it means in the engine | Suggested price |
|---|---|---|
| Extra region or niche | Wider `locations` / more `roles` (a new niche also needs the playbook's source search words to cover it), or a second client file on another niche playbook. A second client file has **its own ledger**, so the two files can repeat each other's companies; the engine does not de-duplicate across them. | + USD 100-200 / month each |
| Verified-email upgrade | Switch on Hunter or Apollo plus a paid verifier (`millionverifier`, `zerobounce`, `neverbounce` or `hunter`), with `budget.max_paid_lookups` | + USD 100-250 / month (must cover the tool plans; check current pricing) |
| Opening lines | `opening_line.enabled: true`; template lines are free; AI lines (`ai: true`) cost a few cents per delivery (5.7) | Template: include free. AI: + USD 25-50 / month |
| Google Sheet delivery | `delivery.google_sheet.spreadsheet_id` in the client file (needs `pip install -e ".[sheets]"` and a Google service account) | + USD 25-50 / month, or included in Growth |
| Exclusivity | You sell one niche + region to only one agency. **The engine does not enforce this** (each client's ledger is separate; two clients can receive the same company). You must track it yourself. | + 25-50% on top of the plan |

**Founding-client offer (suggestion).** For your first 3 clients: pilot at the low end of the
range, and the monthly price locked for 6 months, in exchange for a feedback call after each
of the first 4 deliveries and a short testimonial if they are happy.

**Pricing rules that protect you** (from `PLAN.md`, still valid):

- Charge per week or month, **not per lead**. Volume depends on the job market. Agree a
  *target* (`leads_per_week`), not a guarantee.
- Do not promise "verified emails" unless you pay for finder + verifier, and even then promise
  the **labels**, never a bounce rate.
- Know your cost per delivery before you set a Growth price (next section).

### 5.7 Unit economics per client

*Unit economics* = what one client brings in minus what it costs you to serve them.

#### 5.7.1 What a delivery costs in API fees

The engine counts every request and prints it after each delivery. A *paid lookup* is one
request to a paid provider (engine fact, `leadgen/usage.py`). To see a USD estimate, put
**your plan's** price per request in the playbook:

```yaml
usage:
  cost_per_call: {millionverifier: 0.004, hunter: 0.03, apollo: 0.05}   # USD, from YOUR plan
```

The numbers above are the **example** prices written in the comments of
`playbooks/recruitment-delivery.yaml`. They are not quotes. **Check current pricing.**

The table below was produced with the engine's own `UsageMeter` (the class that prints the
usage lines) for a 25-leads-a-week client, using those example prices. The call counts are
**assumptions** except where marked.

| Setup | Paid lookups per delivery | Estimated API cost per delivery (example prices) | Per month (x 4.33) |
|---|---|---|---|
| Free path (Adzuna + pattern + basic) | 0 (engine fact) | USD 0 | USD 0 |
| Free path + paid verifier only | about 40 verifier calls (assumption; only if people are named, e.g. by a `csv` contact list - Adzuna alone names nobody, so 0 calls) | about USD 0.16 | about USD 0.70 (and guesses never become `verified`; only real addresses from a list can; see 5.4) |
| Hunter finder + paid verifier | 50 Hunter + 40 verifier (assumption) | about USD 1.66 | about USD 7.20 |
| Apollo finder + paid verifier | 100 Apollo + 40 verifier (assumption) | about USD 5.16 | about USD 22.30 |

Where the call counts come from:

- **50 companies enriched** is an engine fact for a 25-lead client: a client delivery enriches
  at most `max(leads_per_week x 2, 10)` companies (`leadgen/delivery/client.py`).
- **Apollo: about 2 calls per company** (one people search, one email reveal; the delivery
  playbook sets `reveal_limit: 1`). Apollo needs a website or an Apollo company id, and
  Adzuna rows have no website, so on Adzuna-only data Apollo may find little (engine fact from
  the adapter's docstring; never tested live).
- **Hunter: about 1 call per company** (domain search). Hunter can also search by company
  **name** when there is no website (engine fact from the adapter's docstring; never tested
  live). This makes Hunter the natural first paid tool for Adzuna-sourced leads.
- **Verifier: 1 to 3 calls per person** (engine fact: up to 3 guessed candidates are checked,
  `MAX_CANDIDATES_TO_VERIFY = 3` in `leadgen/pipeline.py`). Results are cached for 30 days,
  and anything already delivered to the client is removed **before** any paid lookup, so
  repeat costs are lower.

**Plan fees matter more than per-call prices.** Most of these providers sell monthly plans
with a credit allowance (check current pricing). At the start, your real cost is the plan
fee, shared across all Growth clients, not the per-call number. That is why paid tools are
only switched on when a client's price covers them.

**Cap the spend on every paid delivery** with `--budget N` or `budget.max_paid_lookups` in the
client file. When the cap is hit, paid requests are refused **before** they reach the network,
the verifier falls back to the free `basic` checker, and the QA summary warns (engine facts).
Example of what the engine prints with a cap of 60 (produced with the engine's `UsageMeter`,
example prices):

```
API usage: paid lookups 60/60
  finder hunter (paid): 50 calls, ~$1.5000
  verifier millionverifier (paid): 10 calls, 30 skipped by budget, ~$0.0400
  source adzuna (free): 6 calls
  estimated cost: ~$1.5400
```

#### 5.7.2 AI opening lines (optional)

Template lines are free. AI lines use the playbook's `writer` model; each call is capped at
400 output tokens and the whole run at `max_cost_usd` (default USD 0.50) (engine facts,
`leadgen/delivery/opening.py`). Worst-case estimates for 25 lines, from the built-in price
table `LLM_PRICES` in `leadgen/usage.py` (Anthropic list prices cached 2026-06; check current
pricing), with a typical prompt of about 950 characters:

| Model | Worst case per line | Worst case per 25-line delivery |
|---|---|---|
| `claude-haiku-4-5` (cheapest) | about USD 0.0022 | about USD 0.06 |
| `claude-sonnet-5` | about USD 0.0045 | about USD 0.11 |
| `claude-opus-5-5` | about USD 0.0090 | about USD 0.22 |
| `claude-opus-5` (the Anthropic adapter's default model) | about USD 0.0112 | about USD 0.28 |
| Any OpenAI model, e.g. `gpt-5-mini` (not in the price table, so priced at the 10 / 50 fallback unless you set `usage.llm_price_per_mtok`) | about USD 0.022 | about USD 0.56 (the engine's deliberately high estimate, not OpenAI's real price; above the default USD 0.50 cap, so if real spend were this high the last few lines would fall back to templates) |

How these were computed: the engine's own `worst_case_cost` (prompt characters / 4 as input tokens, plus the full 400 output tokens) at the prices in `LLM_PRICES` (table in [2.11](#211-usage-metering-the-budget-and-prices-leadgenusagepy)). The exact figure moves with the prompt length: `claude-opus-5-5` comes to about USD 0.0089 per line at 900 characters and about USD 0.0090 at 950. Real prompts with a job snippet may be longer. Real spend is usually lower than the worst case, and none of these figures has been checked against a real bill.

AI lines are a nice-to-have. They are not a reason to spend money before you have clients.

#### 5.7.3 Your time (the real cost on the free path)

| Task per client per week | Estimate |
|---|---|
| Run `leadgen deliver`, read the QA summary | 5-10 min |
| Spot-check 3-5 rows (open job links, check the person is still there) | 10-25 min |
| Send the files, answer questions | 5-10 min |
| **Total** | **about 20-45 min a week, 1.5-3.3 hours a month** (assumption) |

#### 5.7.4 Margin per client (illustrative)

| Plan (suggested price) | API cost / month | Your time / month | Revenue per hour of your time |
|---|---|---|---|
| Starter, free path, USD 300 | USD 0 | 1.5-3.3 h | about USD 90-200 / h |
| Growth, Hunter + verifier, USD 700 | about USD 15 at example prices for 50 leads/week, **plus** a share of the tool plans (check current pricing) | 2-4 h (assumption) | about USD 175-350 / h before plan fees |

These are illustrations built from the assumptions above. The real numbers come from your
usage lines and your own time log.

### 5.8 Budget allocation for USD 100-200

Rule one: **do not spend on data tools until a client has paid.** The free path is enough to
make samples and run pilots. All amounts below are **estimates; check current prices**.

| Item | USD 100 plan | USD 200 plan | Notes |
|---|---|---|---|
| Domain name (1 year) | 15 | 15 | Estimate; a short, plain business name. |
| Second domain for outreach | 0 | 15 | Only if you will send more than a handful of cold emails a day. The README's advice for outbound: send from separate domains, never your main one. |
| Business email (one mailbox, e.g. Google Workspace or similar), first 2-3 months | 20 | 40 | Estimate; check current pricing. A real business address matters for trust and deliverability. |
| One-page website | 0 | 10 | Free tiers of site builders are usually enough (check). One page: what the report is, a sample image, how to contact you. |
| Adzuna API key | 0 | 0 | Free according to the README (never checked live; confirm Adzuna's current terms). |
| `leadgen`, Python, spreadsheets | 0 | 0 | Free. |
| AI opening lines | 0 | 10 | Optional; template lines are free. |
| **Reserve: first paying client's upgrade** (a Hunter or verifier plan for the first month) | 40 | 70 | Spend **only after** the client's invoice is paid. Free plans / trial credits first where the provider offers them (check). |
| Contingency (payment fees, a surprise cost) | 25 | 40 | Keep it. |
| **Total** | **100** | **200** | |

What you should **not** buy yet: sending tools (Instantly / Smartlead), paid data plans,
LinkedIn automation tools (generally against LinkedIn's terms; check them, not legal advice), ads, a logo designer.

### 5.9 Go-to-market: how to get the first clients

#### 5.9.1 Pick one niche and one region first

One niche + one region means one playbook, one set of search words, one message, and
samples you can make quickly. Pick one where:

- you understand the roles a little (or can learn them in a day),
- a free Adzuna search returns enough fresh jobs (run it and look),
- there are at least 50-100 small agencies you can find (assumption for a first list).

**Engine setup for your niche** (engine fact: the source search words are set in the
playbook, **not** taken from the client's `roles`):

```bash
cp playbooks/recruitment-delivery.yaml playbooks/delivery-<niche>.yaml
# edit: sources -> adzuna -> countries: [..] and queries: [..] for the niche
#       signals -> match_keywords: [..] (a client's roles: will replace these)
leadgen run -p playbooks/delivery-<niche>.yaml --db <scratch>.db --out <scratch-folder>
```

The shipped playbook is set up for finance and accounting roles in the US (`countries: [us]`).
Keep a small library of niche playbooks; every prospect in that niche reuses it.

#### 5.9.2 Build the prospect list

Target: a spreadsheet of 100-200 agencies in your niche and region (target). Sources, all by
hand:

- **Google:** "[niche] recruitment agency [city]", "[niche] staffing [state]". Open each
  site and note the niche, the region, and the owner / MD name from the team page.
- **LinkedIn, by hand:** company search for "[niche] recruitment" with a location filter;
  look at company size (1-20 employees is ideal, a suggestion). Browsing is normal use. **Do not** use
  scraping or automation tools; LinkedIn's terms generally forbid them (read its current user
  agreement; this is not legal advice).
- **Directories:** industry association member lists and business directories, where they
  are public.
- **Job boards:** agencies that post many ads in your niche are active in it. Note them.

For each agency, record: name, website, niche, region, owner / BD lead (role, and name if
public), a published business email or contact form, and one specific detail for
personalisation. Prefer an address the agency publishes itself. Do not send cold emails to
guessed addresses in bulk: bounces damage a new domain's reputation.

#### 5.9.3 Using `playbooks/my-agency.yaml` (optional, needs edits first)

`my-agency.yaml` is an **outbound-mode** playbook for finding clients for your own business.
As shipped (engine facts from the file):

- it targets B2B service firms (recruitment **and** marketing, IT services, consultancies)
  that are hiring sales / BD people, and writes to founders and MDs;
- its `offer` section still pitches "outbound lead generation and AI automation", not the
  Hiring Signal Report;
- its switched-on source, finders and email checker are **paid** (TheirStack; Apollo and
  Hunter, with the free `pattern` guesser after them; MillionVerifier), it needs a website
  for every company (`icp.require_domain: true`), and its writer is AI (`claude-opus-5` via Anthropic), so it does not fit a USD 100-200 budget as is;
- a prospect needs a matching job post to be kept (`signals.require: true`), so a plain list
  of agencies without a job title is rejected.

Suggested edits before you use it (check each with `leadgen validate -p playbooks/my-agency.yaml`):

| Section | Suggested change |
|---|---|
| `offer` | Rewrite `service`, `value_prop`, `proof`, `cta` to pitch the Hiring Signal Report. |
| `icp.industries` | Narrow to recruitment / staffing only. |
| `signals.match_keywords` | Roles that show an agency is growing, e.g. recruiter, recruitment consultant, business development. Or set `signals.require: false` to keep a hand-made list without job posts. |
| `sources` | Switch on the `csv` source (`data/my_prospects.csv`, your spreadsheet); switch off `theirstack` until you have revenue. |
| `writer` | `type: template` (free), or a cheap model such as `claude-haiku-4-5`. |
| or `mode` | `mode: delivery` if you only want a scored prospect list and will write to people yourself. |

Then rehearse for free with `leadgen run -p playbooks/my-agency.yaml --dry-run`. In outbound
mode the engine only hands over a guessed email when a real checker says `valid` (engine
fact, `enrichment.accept_guessed_statuses` default `[valid]`), so on a free setup expect few
hand-over-ready leads. **For the first 30 days, the simplest path is a hand-built spreadsheet
and personal emails from your own mailbox.**

#### 5.9.4 The free-sample method (step by step)

The sample is the whole sales pitch: "here are companies in your niche and region that posted
a job this week."

1. **Make a client file named as if they had signed:**
   ```bash
   leadgen clients new acme-recruiting
   ```
   Edit `clients/acme-recruiting.yaml`: `display_name`, `playbook:` (your niche playbook),
   `roles`, `locations`, `leads_per_week: 10`, and `exclusions.keywords: [staffing, recruiting]`.
2. **Check keys (free):** `leadgen doctor --client acme-recruiting`.
3. **Run a real delivery:** `leadgen deliver --client acme-recruiting`. (A `--dry-run` finds
   nothing on the default playbook because it skips every network source; engine fact.)
   A real run is recorded in the ledger under the prospect's name, so if they sign, their
   first paid file will not repeat the sample leads.
4. **Read the QA summary.** If it says `Low volume` or 0 leads, widen roles or locations, or
   do not send (the engine itself warns "Don't send them to the client" at 0 leads).
5. **Spot-check 3-5 rows:** open the job links; make sure no other agency slipped through.
6. **Send the HTML one-pager** (`deliveries/acme-recruiting/<date>/acme-recruiting-hiring-signals-<date>.html`,
   or print it to PDF) with message 1 below. Keep the CSV / Excel for "full sample on request"
   or the pilot.
7. For a generic sample that isn't tied to one prospect, `leadgen demo -p <playbook> --prospect "Name"`
   makes a "live opportunities" page from the latest run, with emails masked by default.

Note: on the free setup the decision-maker column will mostly say `not found` (5.4). For a
high-value prospect you may hand-research the owner of the hire for the top 3 rows from the
companies' own websites and mention them in your email. A sample shows real people's names to
someone who is not yet a client: keep samples small and see the privacy notes in 5.18.

#### 5.9.5 Outreach templates

Replace everything in [brackets]. Keep each message short and specific. Send from your
business address, 10-20 personal emails a day at first (suggestion), rising slowly.

**Message 1 - first message, with the sample attached**

> Subject: [7] companies hiring [accountants] in [Texas] this week
>
> Hi [First name],
>
> I saw that [Agency] places [finance and accounting] people in [Texas] ([one specific
> detail from their site]).
>
> Every week I pull together the companies that posted a [niche] job in the last 7 days:
> the role, the link, the date posted and, where I can find them, the person who owns the
> hire. Other agencies' ads and re-posted old ads are filtered out as far as possible.
>
> I ran it for [Agency] this week. The one-page summary is attached: [N] companies in
> [region], [M] of them posted in the last 2 days.
>
> Want the full spreadsheet? It's free. Just reply "send it".
>
> [Your name]
> [Business name] · [website]
>
> If you'd rather not hear from me, reply "no" and I won't email again.

**Message 2 - follow-up, 3-4 days later, same thread**

> Hi [First name], a quick follow-up. Since my last email, [N] more companies in [region]
> posted [niche] roles. The earlier you call, the better your chance of getting the job order.
>
> Happy to send the full list for free. Reply "send it", or "not now" and I'll leave you be.
>
> [Your name]

**Message 3 - pilot offer, after the full sample or a call**

> Subject: 4-week pilot for [Agency]
>
> Hi [First name], thanks for [looking at the sample / the call]. Here is what I'd suggest:
>
> - A report every [Monday] morning for 4 weeks, about [15] companies a week
> - Only [roles] in [region], jobs posted in the last 7 days
> - Never the same company, job or person twice; your existing clients and competitors left out
> - Every email honestly labelled: verified, risky, guessed-unverified or not found
>
> Price: [USD X] for the 4 weeks, paid upfront. If you carry on, [the pilot price comes off
> your first month]. After that it's month to month; cancel any time.
>
> If that works, send me the list of your current clients to exclude, and the first report
> will be with you on [date].
>
> [Your name]

Also connect with the owner on LinkedIn by hand, with a one-line note that mentions their
niche. Do not automate it.

#### 5.9.6 Short call script (10-15 minutes)

1. **Open (30 s):** "Thanks for the time. I'd like to ask 3 quick questions about how you
   find new clients, then show you this week's list. OK?"
2. **Discover (3-5 min):** the qualifying questions in 5.3. Listen for hours spent on BD and
   for "we hear about jobs late".
3. **Show (3 min):** open the HTML: the dates ("posted 1 day ago"), the roles, the hot leads.
   Explain the four email labels. Say plainly what the free setup does not include.
4. **Value (1 min):** "If one of these companies became one placement this year, roughly
   what would that fee be?" Let them say the number. Compare it with the pilot price.
5. **Offer (1 min):** the 4-week pilot, paid upfront, cancel any time after.
6. **Close:** "Shall I send the invoice today, so the first report is with you on Monday?"
7. **If not now:** "What would need to be true for this to be useful?" Book a follow-up date;
   offer one more free week at most.

#### 5.9.7 Objection handling

| Objection | Honest answer |
|---|---|
| **"It's too expensive."** | "One placement at your usual fee pays for [N] months of this. The pilot is [USD X] for 4 weeks, and you can stop any time." Offer fewer leads a week at a lower price rather than a discount on the same thing. Work out [N] beforehand from their fee: fee / monthly price (for example USD 6,000 / USD 300 = 20 months; at Growth prices it can be under a year). |
| **"We already use LinkedIn."** | "Keep using it. LinkedIn shows you who works where. This shows who **posted a job in your niche this week**, filtered to your region, with your clients, competitors and old ads removed, and nothing repeated from earlier weeks. There's a LinkedIn URL column where we have one, so the two work together." |
| **"How good is the data? Are the emails accurate?"** | "Every email carries one of four labels, and a guessed address is never called verified. The Excel file has a legend. On the standard plan most rows won't name a person or have an email; with the verified-email upgrade we use paid tools and label each result. I check a few rows by hand every week, and I replace anything clearly wrong in the next report." Never promise a bounce rate. |
| **"How is this different from job boards?"** | "Job boards are built for candidates: you search one term at a time and see the same ads again and again. This is filtered to your exact roles and region, removes other agencies' ads and re-posted ads (as far as possible) and anything we sent before, ranks the hottest leads first, adds the decision-maker where we can find one, and arrives in your inbox every week." |
| **"We don't have time to call more companies."** | "Then take 10 a week, not 25, only the hottest." (`leads_per_week` and `tiers` in the client file.) |
| **"Can you do the outreach for us?"** | "No. We deliver the list; you keep the relationship. That's deliberate." |
| **"Is this legal / GDPR-safe?"** | "The companies come from public job postings; names and emails, where included, come from [the contact tools or lists I use]. Anyone who asks not to be listed is left out of all future reports. You should check the rules that apply to your own outreach; I'm not a lawyer." (See 5.18.) |
| **"Send me more information."** | Send the sample plus a 5-line summary, and propose a 10-minute call with two time options. |

### 5.10 Closing and payment

- **Invoice upfront.** The pilot is paid before the first report. Monthly plans are paid at
  the start of each month. You never carry the risk of unpaid work.
- **Payment:** bank transfer or a payment link from a mainstream payment provider (check its
  fees). Put the fees in your prices.
- **Simple one-page terms** (suggestion; not legal advice):
  1. What's included: plan, niche, region, target leads a week, delivery day, formats.
  2. Volume is a **target, not a guarantee**; quiet weeks happen; surplus leads are held back
     and may appear in a later report if they are still fresh.
  3. What the four email labels mean; no bounce-rate promise.
  4. Use of data: for the client's own business development only; not to be resold or shared.
  5. Removal requests: anyone who asks not to be listed is removed from all future reports.
  6. Make-good: rows that are clearly wrong (another agency, a job outside the date window) are
     replaced in the next report. No refunds for weeks already delivered.
  7. Cancel any time before the next billing date. Price changes with 30 days' notice.
- **Paperwork for you:** check how to register and pay tax on self-employed income where you
  live, and, if you are on a student visa, whether you are allowed to run a business. (Not
  legal or tax advice.)

### 5.11 Onboarding checklist for a new client

| # | Step | How |
|---|---|---|
| 1 | Intake | Collect: roles they fill, roles to exclude, buyer titles (who they call), locations, company size, industries, **list of existing clients**, competitors, leads per week, delivery day, formats, their contact person. |
| 2 | Client file | `leadgen clients new NAME` (or reuse the sample's file), then edit `clients/NAME.yaml`. A typo is caught with "did you mean" (engine fact). |
| 3 | Niche playbook | Point `playbook:` at the playbook whose Adzuna `queries` / `countries` match the client's niche and market. |
| 4 | Exclusions = their existing clients | `exclusions.companies`, `exclusions.domains`, `exclusions.keywords: [staffing, recruiting]` in the client file; for long lists `leadgen suppress add VALUE --client NAME` (or `--file list.csv`; add `--kind company` for company names, otherwise a name that is not an email, domain or LinkedIn URL is refused). Check with `leadgen suppress list --client NAME`. |
| 5 | Keys | `leadgen doctor --client NAME` (free checks only, never a paid lookup). |
| 6 | Paid tools (Growth only) | Switch on Hunter / Apollo and a paid verifier; set `budget.max_paid_lookups`; add `usage.cost_per_call` with your plan's prices. First live run small (`--budget 20`), because no provider was ever tested live. |
| 7 | Rehearsal (optional) | Copy the database and run on the copy (`--db`, `--out` to a scratch folder). Never send rehearsal files. |
| 8 | First delivery | `leadgen deliver --client NAME`, read the QA summary, spot-check 3-5 rows, send the files in the delivery folder, **never** `_internal/`. |
| 9 | Explain the labels | Walk them through the Excel "About" sheet legend. |
| 10 | Feedback call | 2-3 days after the first delivery: which rows were useful, which were not. Adjust `roles`, `locations`, `exclusions`, `leads_per_week`, `tiers`. |
| 11 | Schedule | Add a weekly cron line (README "Weekly automation"), and a calendar reminder to read the QA summary and send. |
| 12 | Back up | Copy `data/*.db` somewhere safe every week. Losing it means re-delivering old leads. |

### 5.12 Delivery SLA and quality promises

An *SLA* (service-level agreement) is what you promise about the service. Promise only what
the engine and your routine can actually guarantee.

| You can honestly promise | Because |
|---|---|
| A report every week on an agreed day | You run it (by hand or cron). |
| Every job was posted within the last 7 days (or their `freshness_days`) as of the delivery date, by the posting date the source gives | Engine fact: freshness filter; undated jobs excluded by default. (Adzuna's date is its `created` field; how closely that matches the employer's real posting date was never checked live.) |
| Re-posted old ads are left out (best effort) | Engine fact: `drop_reposts: true`, based on the job history the engine keeps. A first delivery from a new database has no history yet, so it can include some re-posts (6.7). |
| Nothing already delivered is repeated (company, job or person) | Engine fact: the ledger, **as long as you keep the database safe**. |
| Their existing clients and competitors are excluded | Engine fact: client exclusions and client do-not-list. |
| Other agencies' ads are filtered out (best effort) | Engine fact: `exclude_industries` / `exclude_keywords` in the delivery playbook (plus client keywords). An agency whose name and industry don't give it away can slip through, which is one reason for the weekly spot-check. |
| Every email is honestly labelled; a guess is never called verified | Engine fact: `email_label` in `rows.py`. |
| Anyone who asks is removed from all future reports | `leadgen suppress add ...` (global). The global list is per database file, so keep every client on the same database (4.8). |

| Do NOT promise | Why |
|---|---|
| An exact number of leads | The job market varies. Promise a target. |
| A bounce rate or "100% verified" | Even checkers can't always tell (catch-all servers). |
| That every job is still open, or the person still works there | Data ages; that's why you show the posting date and spot-check. |
| Placements or revenue | Depends on the agency's own work. |
| Exclusivity, unless you sold it and track it yourself | The engine keeps each client's history separately. |

If a delivery comes out at 0 leads, do not send an empty file: tell the client, explain why
(the QA summary says), and widen the search for next week.

### 5.13 The weekly routine (short version)

The full routine, with what to look for at each step, is in [4.6](#46-the-weekly-routine) (and `PLAN.md` section 3). In short, for each client every week:

```bash
leadgen clients                     # who is due, what they received so far
leadgen deliver --client NAME       # files + QA summary
```

Read the QA summary, spot-check 3-5 rows, send the delivery folder (not `_internal/`), log
the numbers in your tracking sheet (5.15). Monthly: `leadgen doctor --client NAME`, check
volume trends and the "already delivered" share, back up `data/*.db`.

### 5.14 Retention and upsell

Clients renew when the report produces conversations and job orders. So:

- **Monthly outcome check-in (10 min):** "How many of last month's leads did you contact? How
  many conversations, job orders, placements?" Record it. This is your proof and your renewal.
- **Act on feedback within one delivery:** change the client file the same day.
- **Watch for a niche running dry:** many "already delivered to this client" reasons in the
  QA summary means the niche is exhausted at current settings. Widen roles or locations, or
  add sources, **before** the client notices lower volume.
- **Upsell when there is evidence:** a client who reports a job order is ready for the
  verified-email upgrade, an extra region or niche, or Google Sheet delivery.
- **Ask for referrals** once a client reports a win (suggestion: one free month for a referral
  that becomes a paying client). Referrals must not be in the same niche and region as the
  referrer, unless you have agreed they can share.
- **Annual prepay (suggestion):** two months free for paying a year upfront, once a client has
  been with you three months.
- **Case study:** with permission, a two-sentence story ("Agency X, finance in Texas, won a
  job order in week 2") is the best line in your outreach (put it in `offer.proof` if you use
  `my-agency.yaml`).

### 5.15 KPIs and the tracking sheet

A *KPI* (key performance indicator) is a number you watch to know if the business is working.

| KPI | Formula / where it comes from | Target (not a forecast) |
|---|---|---|
| Samples sent | Count per week (your sheet) | 25-50 a week in month 1 |
| Reply rate | Replies / samples sent | The README gives a rough target of a 3-8% reply rate for good cold outbound (a rule of thumb, not measured data); a personal message with a real sample should aim higher, e.g. 10%+ (target) |
| Calls booked | Count (your sheet) | 1 per 10 replies or better (target) |
| Pilots started | Paid pilots / calls | 1 in 3 calls (target) |
| Pilot -> monthly conversion | Monthly clients / pilots finished | 50%+ (target) |
| Monthly churn | Clients lost this month / clients at start of month | Under 10% (target) |
| MRR (monthly recurring revenue) | Sum of monthly plan prices | See 5.16 |
| Leads delivered vs target | QA summary "delivered (target N)"; `leadgen clients` total | At or near target most weeks |
| Verified-email rate | QA summary "verified email rate" | Growth clients: track the trend; free setup: expect low |
| "Already delivered" share | QA summary top reasons | Rising = niche drying up |
| Cost per delivery | Usage lines + `usage.cost_per_call` | Well below the plan price |
| Client-reported job orders / placements | Monthly check-in | At least 1 job order per client per quarter (target) |

For your **own** outreach, if you use `my-agency.yaml` in outbound mode, the engine also keeps
a funnel: `leadgen stats -p playbooks/my-agency.yaml`, and `leadgen mark ... --stage booked|won|lost`
moves a prospect by hand (engine facts).

#### 5.15.1 Tracking sheet layout (Google Sheets or Excel, three tabs)

**Tab 1 - Prospects**

| Agency | Website | Niche | Region | Decision-maker role | Found via | Sample sent (date) | Follow-up 1 (date) | Follow-up 2 (date) | Replied (Y/N) | Call (date) | Pilot offered (date) | Pilot paid (date, USD) | Became monthly (Y/N) | Lost reason | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|

**Tab 2 - Clients**

| Client (file name) | Plan | Price / month | Start date | Next invoice | Paid (Y/N) | Niche playbook | Paid tools on | Last check-in | Calls reported | Job orders reported | Placements reported | Churn date | Churn reason |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|

**Tab 3 - Weekly deliveries** (one row per client per week, copied from the QA summary)

| Week | Client | Delivered | Target | Hot | Verified email rate | Top reason left out | Warnings | Paid lookups | Est. cost (USD) | Minutes spent | Sent (date) |
|---|---|---|---|---|---|---|---|---|---|---|---|

Summary cells at the top of Tab 1: samples this week, reply rate, calls, pilots, conversion.
At the top of Tab 2: number of clients, MRR, churn this month.

### 5.16 The 7-day, 30-day and 90-day plan

All numbers in this section are **targets**, not forecasts. Hitting half of them is still a
real start.

#### 5.16.1 First 7 days

| Day | Actions | Done when |
|---|---|---|
| 1 | Install, run the offline demo (`leadgen deliver --client demo-client`), open the three files. Get a free Adzuna key; fill `.env` (`ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `SENDER_NAME`, `SENDER_EMAIL`, `SENDER_WEBSITE`). | You can explain every column of the demo file. |
| 2 | Choose one niche + one region. Copy the delivery playbook to `playbooks/delivery-<niche>.yaml`, set `countries` and `queries`. `leadgen doctor -p ...`, then a real `leadgen run -p ...` into a scratch folder. Buy the domain and set up the mailbox. | A real run returns fresh jobs in your niche. |
| 3 | Build the prospect sheet: 50 agencies (5.9.2). Set up the tracking sheet (5.15). | 50 rows with niche, region, contact route. |
| 4 | Make 10 samples (5.9.4). Spot-check each. | 10 HTML one-pagers you'd be proud to send. |
| 5 | Send 10 samples with message 1; connect on LinkedIn by hand. Write your one-page terms and an invoice template. | 10 sent, terms ready. |
| 6 | 10-15 more samples. | 20-25 sent in total. |
| 7 | Send follow-ups (message 2) to day-5 prospects. Review: which subject lines got replies? Improve. | Numbers logged. |

**7-day targets:** 20-30 samples sent, 1-3 replies, 1 call booked.

#### 5.16.2 Days 8-30

- **Every weekday:** 5-10 new samples, follow-ups due that day, reply within hours.
- **Every call:** the script in 5.9.6; send message 3 (pilot offer) the same day.
- **As soon as a pilot is paid:** onboarding checklist (5.11), first delivery within 3 working
  days, feedback call 2-3 days later.
- **Weekly:** review the KPIs; drop the message variant that performs worst.

**30-day targets:** about 100 samples sent, 5-10 conversations, **2-3 paid pilots**, first money
in by around day 14-21, all pilots delivered on time every week.

#### 5.16.3 Days 31-90

- Convert pilots to monthly plans at the end of week 4 (founding-client offer, 5.6).
- Keep prospecting at a steady 25-50 samples a week until you hit your client target.
- Once one client pays for Growth: switch on the first paid tool (Hunter is the natural first
  for Adzuna-sourced leads), with `--budget` and `usage.cost_per_call`, after a small live test.
- Automate: a cron line per client, weekly `leadgen doctor`, weekly database backup.
- Collect one testimonial or case study; ask each happy client for one referral.
- Add a second niche playbook only when the first niche has 3+ clients or runs dry.

**90-day targets:** **3-6 paying monthly clients**, which at USD 300-450 a month (the upper part
of the suggested USD 250-450 Starter range) is roughly USD 900-2,700 MRR; churn of 0-1 client; at least one client reporting a job
order that came from the report.

### 5.17 Scaling paths

| Path | What it means | What the engine already has | What would need to change |
|---|---|---|---|
| **More niches and regions** | Same product, more agencies | Niche-agnostic engine; one playbook per niche; one client file per client | Deriving source search words from each client's `roles` (today you copy a playbook per niche; flagged in `PLAN.md` as worth automating). |
| **Other signal products** | e.g. funding signals for SaaS vendors and agencies, expansion signals, local-business review signals | Templates `saas-funding`, `local-business` (outbound mode) and signal types funding / expansion / leadership change / headcount growth; delivery selection accepts other *primary* signal types | The report is hiring-specific: file names `-hiring-signals-`, the "Jobs posted in the last N days" notes, column headers (Job title(s), Job link, Date posted), and a job posting is always added as a primary signal (`ensure_job_postings_primary` in `leadgen/delivery/run.py`). Relabel these per product. Funding data needs a paid source (Apollo in the template). |
| **Productised service** | Fixed packages, a standard intake form, fixed delivery day | Client files, weekly cron, QA summary, Google Sheets push | Plan presets in client files; an intake form that writes the client file. |
| **Hire a virtual assistant (VA)** | A part-time assistant runs deliveries, spot-checks and prospect research | The weekly routine is 2-3 commands plus a checklist | A written SOP (standard operating procedure) from `PLAN.md` section 3 and this section; shared access without sharing `.env` keys widely. |
| **Software (later)** | A client portal, self-serve sign-up, billing | Not built (listed in `PLAN.md` as not built: client portal, billing, CRM sync, emailing the report automatically on purpose) | A web app on top of the delivery layer. Only worth it with many clients. |

### 5.18 Risks and compliance

None of this is legal advice. Where rules matter, read the provider's terms and your local
rules, or ask a professional.

| Risk | What to do |
|---|---|
| **Data-provider terms on reselling** | Many providers restrict redistributing their data. Apollo, Hunter, TheirStack and Adzuna **may** restrict sharing their data with your clients. Read each provider's terms before selling a report built on it. Job ads usually belong to the employer or the job board. Prefer companies' own public job boards (Greenhouse / Lever / Ashby, free in the engine), official APIs whose terms allow your use, and your own research. |
| **Personal data** | The report contains names, job titles and work emails of real people. Many places regulate this (for example UK / EU data-protection law). Consider lawful basis, telling people, opt-outs, and how long you keep data. Honour every "don't list me" with `leadgen suppress add ...` (global) so no client receives that person again. Keep samples small. |
| **Your own cold outreach** | Cold-email rules differ by country. Include an opt-out line, honour it at once, send from a real business address, keep volume low. |
| **Honest email labelling** | Never relabel or "clean up" labels by hand to look better. The labels are the product's credibility. |
| **LinkedIn / Indeed scraping** | Against those sites' terms, as the README and the code's own warnings say (check the current terms yourself). The adapters exist (`linkedin_jobs`, Apify presets) but are marked "use at own risk", flagged by `leadgen validate`, and excluded from every default playbook. Do not build the business on them. |
| **Untested live connectors** | No provider has been called live. Before relying on one: `leadgen doctor`, then a small real run with `--budget 20`, then fix any differences. |
| **Losing the ledger** | `data/*.db` holds what every client received. Back it up weekly. A lost database means repeated leads. |
| **Client data leaks** | `deliveries/`, `data/*.db`, `.env` are git-ignored (engine fact). Never send `_internal/` to a client; send each client only their own folder. |
| **Thin volume in narrow niches** | The QA summary warns below target. Widen roles or locations, raise `freshness_days` (and the source's date window), or add watchlists / CSV sources. Don't sign a client you can't serve: run the sample first. |
| **Two clients in the same niche and region** | Each client's ledger is separate, so both can receive the same company. Tell them, or sell exclusivity and track it yourself. |
| **Depending on one client** | Keep prospecting until no client is more than a third of revenue (suggestion). |
| **Student-specific** | Check tax registration, and visa work rules if they apply to you. |

### 5.19 Current stack vs business needs: what exists today and what each will need later

This table is for the owner and for any developer or AI assistant continuing the work. "Today"
means verified on branch `claude/modest-hopper-lue9ct`.

| Business step | Supported today (command / file) | Change needed later |
|---|---|---|
| Free sample for a prospect | `leadgen clients new`, `leadgen deliver --client` (HTML hides emails); `leadgen demo -p ... --prospect` (masks emails) | A "sample" option: fewer rows, a SAMPLE watermark, masked names; today you send the HTML by hand. |
| Niche setup | Copy `playbooks/recruitment-delivery.yaml`, edit Adzuna `queries` / `countries` | Build source search words from the client's `roles` automatically. |
| Pricing tiers | Per-client `leads_per_week`, `tiers`, `delivery.formats`, `opening_line`, `delivery.google_sheet`, `budget.max_paid_lookups` | A `plan:` field or presets; a monthly per-client cost report. |
| Verified-email upgrade | Hunter / Apollo finders, MillionVerifier / ZeroBounce / NeverBounce / Hunter verifiers, all `enabled: false`; honest labels | Live testing with real keys; confirm Hunter's name-only search on Adzuna companies; real per-call prices in `usage.cost_per_call`. |
| Exclusivity | Not enforced (per-client ledger) | A cross-client overlap warning for clients sharing a niche and region. |
| Sending the report | By hand (deliberately); optional Google Sheets push | Optional: email the report after the owner has reviewed the QA summary. |
| Billing and invoices | Not built | Keep manual, or connect a payment provider later. |
| KPIs and client outcomes | `leadgen clients`, QA summary (`qa.txt` / `qa.json`), usage lines; outbound `leadgen stats` / `mark` for your own prospects | Store client-reported calls / job orders / placements; export weekly QA numbers to the tracking sheet. |
| Weekly automation | One cron line per client; exit codes 0 / 1 / 2 | `deliver` for all clients in one command; alert on non-zero exit. |
| Own prospecting | `playbooks/my-agency.yaml` (outbound, paid tools, old offer text) | Rewrite the offer; a free-by-default variant for agencies only. |
| Other signal products | Signal types and templates exist; delivery selection accepts other primary signals | Relabel file names, notes and column headers per product. |
| Backups | Manual copy of `data/*.db` | A scheduled backup command. |

### 5.20 The pitch in 30 seconds

> "I help specialised recruitment agencies find new clients faster. Every week I send you a
> short list of companies in your niche and region that posted a job in the last 7 days:
> the role, the link, the date, and where I can find them, the person who owns the hire, with
> every email honestly labelled. Your existing clients and anything I've sent before are
> taken out, and other agencies' ads are filtered out as far as possible, so every row is new. One placement a year can pay for it more
> than once. Can I send you this week's list for your niche, free?"

**One-line version:** "Every Monday: the companies in your niche that posted a job this week,
and who to call where we can find them, without the hours of job-board searching."

---

## 6. Status, limitations and roadmap

> **Snapshot:** 2026-09-25, branch `claude/modest-hopper-lue9ct`, last commit `03ec275`.
> Everything marked "verified" in this section was re-run while writing it, in the
> development sandbox (Linux), using scratch folders for every database and output
> (`--db` / `--out`), so the repository stayed clean. That sandbox has **no outbound
> internet access**, so **no external provider (Adzuna, Hunter, Apollo, Anthropic, ...)
> has ever been called live**. Every connector has only been tested against *canned
> responses*: made-up answers written in the format each provider documents.

### 6.1 The short version (for the owner)

- **The product works end to end on sample data.** `leadgen deliver --client demo-client`
  produces the CSV, Excel and HTML report and the QA summary, and it remembers what was
  delivered so the next run doesn't repeat it.
- **2,073 automated tests pass** on both Python 3.11 and Python 3.9.
- **It has never talked to a real data provider.** Your first real run is also the first
  real test. Section 6.6 explains how to do it safely: start with the free Adzuna key,
  on a copy of the database, with a small `--budget`.
- **The old cold-email engine (outbound mode) is still there, switched off by default.**
  `playbooks/my-agency.yaml` uses it to find clients for *your* business. Rewrite its
  sales pitch before you use it.
- **The biggest practical limits today:**
  - The free setup finds companies and jobs, but usually **no named person or email**.
  - A client in a new niche needs **its own copy of the playbook**, because the search
    words live in the playbook, not in the client file.
  - All the memory (what each client received, the do-not-lists) is **one SQLite file**
    that you must back up.

### 6.2 Snapshot at a glance

| Item | Value | How it was checked |
|---|---|---|
| Branch / last commit | `claude/modest-hopper-lue9ct` / `03ec275` "Foundation fixes from the delivery review" | `git status`, `git log` |
| History | 19 commits, 2026-09-24 13:18 UTC to 2026-09-25 04:48 UTC. All written in Claude Code sessions: every commit's author is `Claude <noreply@anthropic.com>`. | `git log` |
| Code size | about 25,900 lines of Python in 73 files under `leadgen/`; about 20,200 lines in 35 files under `tests/` | `wc -l` |
| Tests | **2,073 passed** in 23.7 s on Python 3.11.15, and **2,073 passed** in 34.9 s on Python 3.9.23 (a scratch virtual environment, with the repo on `PYTHONPATH`) | `pytest -q` |
| Python 3.9 grammar | all 108 `.py` files in `leadgen/` and `tests/` parse under Python 3.9 grammar | `ast.parse(..., feature_version=(3, 9))` |
| Continuous integration (CI: tests run automatically on every push) | **none**: there is no `.github/` folder, so tests are run by hand | `ls -a` |
| Dependencies | `requests`, `PyYAML`, `openpyxl`; optional extras `[sheets]` (gspread) and `[dev]` (pytest) | `pyproject.toml` |
| Declared Python | `requires-python = ">=3.9"` | `pyproject.toml` |
| Live provider calls ever made | **none** | the sandbox blocks outbound network |

> Note for future readers: earlier project notes said Python 3.9 was "only checked
> statically" (by reading the code, not running it). That is now out of date: the full
> suite ran green on 3.9.23 for this write-up. Still **not** checked: Windows, macOS, and
> Python 3.12 or newer.

### 6.3 What is built and working (verified)

**What was run for this section**

| Check | Command (scratch paths shortened to `$R`) | Result |
|---|---|---|
| Test suite | `pytest -q` | 2073 passed |
| Offline demo delivery | `leadgen deliver --client demo-client --db $R/demo.db --out $R/deliveries/{client}/{date}` | 25 companies found, 21 with a live signal, 14 match the client, **10 delivered (target 10), 4 hot**, 2 held back. Email status: **verified 5, risky 3, guessed-unverified 1, not found 1**. 0 API calls. Exit 0. |
| Same command again (the ledger) | same | **2 delivered** (the 2 held back last time). Top reason: "10 already delivered to this client". Files went to `...2026-09-25-2` ("nothing was overwritten"). Low-volume warning. Exit 0. |
| Same command a third time | same | 0 delivered, "Nothing to send this time", files to `...-3`. **Exit 1.** |
| Dry run on the default live playbook | `leadgen deliver --client acme --clients-dir $R/clients --dry-run ...` (a copy of `clients/_template.yaml`) | 0 leads, plus the note "every source uses the network and --dry-run skips them". Exit 1. As documented. |
| Key check, contacting nobody | `leadgen doctor -p playbooks/recruitment-delivery.yaml --dry-run` (no Adzuna keys set) | `MISSING KEY source adzuna`, exit 1 |
| Offline config check | `leadgen validate -p playbooks/my-agency.yaml` | 5 `[FAIL]` lines, all missing keys (TheirStack, Apollo, Hunter, MillionVerifier, Anthropic). Exit 1. |
| Outbound demo | `leadgen run -p playbooks/demo-offline.yaml --db $R/out.db --out $R/output` | 11 leads in `opportunities.csv`; **7 handed over** to `instantly_upload.csv` and `smartlead_upload.csv`. 0 API calls. Exit 0. |
| Reply sorting | `leadgen replies -p playbooks/demo-offline.yaml --file examples/data/demo_replies.csv ...` | 12 sample replies sorted: positive 3, referral 1, timing 1, question 1, negative 1, unsubscribe 1, ooo 1, bounce 1, other 2 |
| Outbound gate | `leadgen followups` (no `-p`); `leadgen serve -p playbooks/demo-delivery.yaml` | Both refused with the "outbound-mode feature" message. **Exit 2.** |
| Client list | `leadgen clients --db $R/demo.db` | demo-client: 2 deliveries, 12 delivered in total |

**Feature status**

| Area | Status | Notes |
|---|---|---|
| Client files (`clients/<name>.yaml`) + `leadgen clients` / `clients new` | Working, offline-verified | Typos get a "did you mean" suggestion; every error is listed at once. |
| `leadgen deliver` (CSV / XLSX / HTML, QA summary, `_internal/`) | Working, offline-verified | Never overwrites an earlier delivery (`-2`, `-3`, ...). |
| Honest email labels (`leadgen/delivery/rows.py`) | Working, offline-verified | Exactly `verified` / `risky` / `guessed-unverified` / `not found`. A guess always wins over a checker's "valid". |
| Ledger (`leadgen/delivery/ledger.py`) | Working, offline-verified | Never re-delivers a company, job or contact to the same client. Held-back leads are not recorded. Dry runs record nothing. |
| Freshness filter (default 7 days) and repost detection | Working, but see the repost limitation in 6.7 | Repost detection needs job history from earlier runs. |
| Do-not-lists (global + per client) | Working, tested with fakes | Companies and domains are dropped before any paid lookup; people are skipped as soon as they are found. |
| `--budget` / paid-lookup cap (`leadgen/usage.py`) | Working, tested with fakes | Refuses a paid request **before** it reaches the network. |
| Usage and cost lines | Working, tested with fakes | Estimates are only as good as the prices you enter (`usage.cost_per_call`). |
| Suggested opening lines | Template lines verified offline. AI lines tested only with a fake AI (`FakeLLM`). | AI lines have a per-delivery cost cap (`max_cost_usd`, default 0.50). |
| `leadgen doctor` | Dry-run mode verified. The live checks are tested only with fakes. | Uses free endpoints only (account, credits or model-list calls). |
| Google Sheets push | Tested only with a fake `gspread` module | `gspread` is not even installed in the sandbox. |
| Outbound mode (writer, hand-over, replies, follow-ups, webhook server) | Working offline; off by default | The webhook server is tested on `127.0.0.1` only. |

**Where the 2,073 tests are.** Grouped by test file (some files cover more than one area): delivery layer 494, outbound-only features 478 (`test_replies` alone has 251), shared engine 1,101. The per-file counts are in [2.18](#218-testing-strategy-tests).

### 6.4 Switched off but kept

These features exist and pass their tests, but nothing uses them by default.

| Feature | Where | Default | How to switch it on | Check before you do |
|---|---|---|---|---|
| **Outbound mode**: AI or template email sequences, hand-over to Instantly / Smartlead (CSV or API) or a webhook, reply sorting, follow-ups, webhook server | `leadgen/writer/`, `leadgen/outbound/`, `leadgen/replies.py`, `leadgen/server.py` | Off: `mode: delivery` is the engine default | Put `mode: outbound` in a playbook. `playbooks/my-agency.yaml` and `playbooks/demo-offline.yaml` already have it. | Rewrite `my-agency.yaml`'s `offer` (`service`, `value_prop`, `proof`) and `writer.extra_instructions`: they still pitch "outbound lead generation and AI automation" and "meetings already booked", not the Hiring Signal Report. |
| Paid tools in the default delivery playbook: the TheirStack source and the Apollo and Hunter finders | `playbooks/recruitment-delivery.yaml` | `enabled: false` | Set `enabled: true` and put the key in `.env` | Set a `budget` in the client file, or pass `--budget`. |
| Free job-board watchlists (Greenhouse, Lever, Ashby) and a CSV import | same playbook | `enabled: false` | Replace the example company boards or path, then `enabled: true` | These are free; they only need the company list. |
| AI opening lines | client file `opening_line: {enabled, ai, max_cost_usd}` | `false` / `false` / `0.50` | `enabled: true, ai: true`, plus `writer: {provider: anthropic, model: claude-haiku-4-5}` in the playbook or the client's `overrides` | `ANTHROPIC_API_KEY`; see the AI limitations in 6.7. |
| Google Sheets push | client file `delivery.google_sheet.spreadsheet_id` | empty | `pip install -e ".[sheets]"`, a service account, share the sheet | Never tested against Google (see 6.5). |
| Slack / webhook alerts | `notify.channels` | commented out | Uncomment and set `SLACK_WEBHOOK_URL` / `LEADGEN_WEBHOOK_URL` | `doctor` skips these on purpose: a test would post a real message. |
| LinkedIn / Indeed scraping (`linkedin_jobs`, Apify presets `linkedin_jobs` / `indeed_jobs`) | `leadgen/sources/apify.py` | Not in any shipped default playbook | Possible, but **not recommended** | Marked "use at own risk": scraping goes against those sites' terms. `leadgen validate` warns about it. |

**How to switch outbound mode on (for your own prospecting):** the steps (rewrite `offer` and `writer.extra_instructions`, fill `SENDER_*` and `BOOKING_LINK`, `validate`, `doctor`, a `--dry-run` whose upload files are named `*.dry-run.csv` and must never be imported, a small paid test, review, import) are in [4.13](#413-outbound-mode-operations-brief). **Always pass `--budget` with `my-agency.yaml`**: it switches on TheirStack, Apollo, Hunter, MillionVerifier and the Anthropic AI writer (`claude-opus-5`), and it sets no `usage.max_paid_lookups` of its own, which means no cap.

`leadgen leads`, `stats`, `demo` and `mark` work in both modes. `mark` is listed under
"Outbound mode only" in the README, but it is **not** gated: on a delivery playbook it runs
and only changes a stored lead's stage. That is harmless, but the README is inaccurate here.

### 6.5 Never tested live: every network adapter

"Adapter" means a small plug-in class that talks to one outside service. Each is listed in
`leadgen/registry.py`. `leadgen adapters` prints the same list. Every network adapter was written
from the provider's public documentation and tested **only** against canned responses fed
through `tests/fakes.py` (`FakeHttp`, and `FakeLLM` for AI). The expected response shape is
written in each module's docstring (the comment at the top of the file). When the real
service answers differently, that docstring is where to compare.

The full list, with each adapter's service host, key, paid status and whether a shipped playbook uses it, is the table in [2.13](#213-adapter-registry-and-plugins-leadgenregistrypy). Every network adapter in that table belongs in this "never tested live" group.

**Other network touch points, also never tested live:**

- **The delivery Google Sheets push** (`push_google_sheet` in `leadgen/delivery/formats.py`).
  It uses `gspread` directly, not `Adapter.http`. Tests swap in a fake `gspread` module.
- **Every `leadgen doctor` check.** Each calls one free endpoint per key (the table is in
  the `leadgen/doctor.py` docstring). The code itself marks the TheirStack check as
  **UNCERTAIN**: TheirStack documents its billing endpoint less formally. If it fails, set
  `doctor_url` on that source.
- **`leadgen serve`** (the reply webhook server). It is tested only on `127.0.0.1` inside
  the test suite, and has never received a real Instantly or Smartlead webhook.

### 6.6 Plan to go live safely

The rule: **free checks first, then a rehearsal that records nothing real, then a small
paid run, then look at the rows with your own eyes.** Do this once for each new
provider you switch on.

| Step | Command | What it proves | Cost |
|---|---|---|---|
| 0. Back up | `cp data/leadgen.db data/leadgen-backup-YYYY-MM-DD.db` (skip if it doesn't exist yet) | You can undo anything | free |
| 1. Offline check | `leadgen validate -p playbooks/recruitment-delivery.yaml` | Config loads, adapter types exist, keys are set | free, no network |
| 2. Key check | `leadgen doctor --client acme` | Each key is accepted, with one free call per key (never a paid lookup, never counted against `--budget`) | free |
| 3. Dry run | `leadgen deliver --client acme --dry-run` | The client file and playbook merge correctly. With the default playbook the result is **empty on purpose**: dry runs skip every network source. | free, no network |
| 4. Rehearsal, small budget | `cp data/leadgen.db data/rehearsal.db` then `leadgen deliver --client acme --db data/rehearsal.db --out output/rehearsal/{client}/{date} --budget 20` | The provider's real answers flow through the whole pipeline. Only the copy records anything. | Free with Adzuna only. With paid tools, at most 20 paid requests. |
| 5. Spot-check | Open the rehearsal files and `_internal/` | See the checklist below | your time |
| 6. Real delivery | `leadgen deliver --client acme` (add `--budget N` if paid tools are on) | The ledger records what was sent | as budgeted |
| 7. Send | the files named on the `Next:` line, never `_internal/`, PREVIEW or rehearsal files | | |

Never send a rehearsal's files. The real ledger doesn't know about them, so the next real
delivery would repeat those leads.

**Spot-check list for the first real run of each provider:**

- **Job links:** open 3-5 of them. Are the jobs real, live and posted when the "Posted"
  column says?
- **Fields:** are company name, website, location and date filled, and in the right
  columns? (A wrong field mapping is the most likely live bug.)
- **People:** open 2-3 LinkedIn URLs. Is the decision-maker still at the company, with
  that title?
- **Email labels:** check a few. A pattern guess must say `guessed-unverified`. A
  provider-supplied, checker-confirmed address may say `verified`.
- **Usage lines** (`API usage: paid lookups N/cap`): compare them with the provider's
  dashboard. `--budget` counts **requests**, and some providers charge per result or per
  credit (see 6.7).
- **Top reasons in `qa.txt` and `not_delivered.csv`:** do they make sense? If
  everything was dropped for one reason, the configuration is wrong, not the market.

**Suggested order for switching providers on** (a suggestion, based on cost and value, not
on measured results):

1. Adzuna (free): the default source.
2. The free Greenhouse / Lever / Ashby watchlists, or your own CSV list: more volume at no cost.
3. Hunter (small free plan): names and emails.
4. One paid email checker (for example MillionVerifier): more `verified` labels. Only
   once a client pays for it.
5. Apollo or TheirStack: better company and people data. Only when a client's price
   covers it.
6. AI opening lines with `claude-haiku-4-5` (the cheapest model in the built-in price
   table), keeping the default `max_cost_usd: 0.50` cap.
7. Google Sheets, Slack: conveniences, last.

**When a real provider answers differently from the canned responses:**

1. Save an anonymised copy of the real response. Remove keys and real people's details.
2. Add a test with that response shape to the provider's test file, using `FakeHttp`.
3. Fix the adapter's parsing until the test passes.
4. Run `pytest -q`, then update the module docstring's "Response" example.

### 6.7 Known limitations

**Data and lead quality**

| Limitation | Why it matters | Workaround today | Possible fix |
|---|---|---|---|
| **Repost detection needs history.** A re-posted ad (an old job put up again to look new) is recognised by comparing with jobs seen in *earlier runs* (`Store.observe_signals`). No shipped source flags reposts itself. | A client's first delivery from a fresh database can include re-posted ads. History is kept per database file. | Accept it for week 1, or spot-check dates. | Seed history, for example with a `leadgen run -p <that playbook>` against the same database a week before the first delivery. Signal history is shared across playbooks, but whether this seeds it well enough is untested. |
| **Company-level dedupe holds back new jobs at known companies.** With the default `dedupe: [company, job, contact]`, a company goes to a client **once, ever**. | A long-running client never hears about a company's *next* relevant opening, so the pool shrinks over the months ("already delivered" grows in the QA summary). | Remove `company` from `dedupe`: new jobs at known companies are then allowed, and already-sent jobs and people are still stripped. Or set `redelivery_days: 90`. | Decide a default per plan: maybe "new jobs allowed" for clients older than a few months. |
| **The free path yields mostly `not found`, `guessed-unverified` or `risky` emails.** Adzuna names no people. The `pattern` finder only guesses, and guesses are always `guessed-unverified`. The free `basic` checker can't confirm a mailbox. | With only free tools, the decision-maker and email columns are mostly empty. `verified` comes only from a list or provider that marks an address as verified. The demo's 5 of 10 verified comes from its made-up contact list; it is **not** a benchmark. | Sell it as a hiring-signal list; add your own CSV contact list; switch on Hunter / Apollo / a paid checker when a client pays. | None needed in code; this is a pricing and positioning decision. |
| **Held-back leads may go stale.** Leads over `leads_per_week` are not recorded, so they can go out next time, but next week their jobs are a week older. | With `freshness_days: 7` some may no longer qualify. | Set `leads_per_week` near the real volume. | Not a bug; worth watching. |
| **Data can be out of date.** A job may already be filled, or a person may have left. | Clients judge the list by accuracy. | The weekly spot-check (see `PLAN.md` §3). | - |

**Product and business**

| Limitation | Why it matters | Workaround today | Possible fix |
|---|---|---|---|
| **Search words don't follow the client's `roles`.** Adzuna `queries` and TheirStack `job_titles` are set in the playbook. The client's `roles` only decide which fetched jobs *count*. | A nursing client on the finance-tuned default playbook gets almost nothing. | Copy `recruitment-delivery.yaml` per niche and point the client's `playbook:` at the copy. | Derive source queries from `roles` (listed as a risk in `PLAN.md` §6). |
| **Report wording is hiring-specific.** The signal label is "Hiring" (`rows.py`), the default brand is "Hiring Signal Report", file names are `<client>-hiring-signals-<date>`, and the AI opening-line prompt talks about hiring. | The engine also knows funding / expansion / leadership-change signals, but the report assumes a job posting. | `branding.brand_name` changes the title. The file names and the "Hiring" label are in code. | Make the labels and file names signal-neutral if you ever sell another signal type. |
| **One command per client; nothing sends the report.** There is no "deliver to all clients" command, no emailing of the report (on purpose: review first), no client portal, no billing, no CRM sync. | Manual work grows with each client. | One cron line per client (README "Weekly automation"). | See the roadmap (6.9). |
| **No legal or country rules built in.** | The report names real people with work emails, and data providers may restrict reselling their data. | Read the provider terms and the privacy rules before selling (`PLAN.md` §6). Use `leadgen suppress`. This is not legal advice. | - |

**Technical**

| Limitation | Details | Workaround / note |
|---|---|---|
| **Nothing tested live** | See 6.5. | Follow 6.6. |
| **`--budget` counts requests, not money or credits** | One paid request = one "paid lookup". TheirStack charges per job returned (the playbook comment says so), and other plans charge per result or credit. | Compare the usage lines with your provider dashboard. Set `usage.cost_per_call` from your plan to get estimates. |
| **SQLite, single user** | One file (`storage.path`, default `data/leadgen.db`) holds the ledger, do-not-lists, job history and email-check cache. It is built for one person running one command at a time. The webhook server is single-threaded. No test covers two runs writing at once. | Don't run two deliveries on the same database at the same time. Back the file up. On a server or GitHub Actions the file must survive between runs, or the ledger forgets what was delivered. |
| **Python and OS coverage** | Declared `>=3.9`. The suite passed on 3.9.23 and 3.11.15 on Linux. Not run on 3.12+, Windows or macOS. There is no CI. | Add CI (roadmap). |
| **Anthropic is called by raw HTTP, not the official SDK** | `leadgen/llm/anthropic.py` POSTs to `https://api.anthropic.com/v1/messages` with header `anthropic-version: 2023-06-01`, through `Adapter.http`, so every call is metered and capped by `--budget`. It handles `stop_reason` `max_tokens` (truncated), `refusal` and empty output itself. | This keeps one HTTP path and no extra dependency. The downside: new API features must be wired by hand. The official Python SDK is Anthropic's recommended route, but its 1.x line needs Python 3.10+, so adopting it means raising the Python floor or keeping metering some other way. |
| **No refusal fallbacks configured** | When a model refuses (`stop_reason: "refusal"`), leadgen raises `LLMError` and falls back to the **free template**: a template opening line, a template email sequence (outbound), or the rules-based reply classifier. No second model is tried. Anthropic offers a server-side `fallbacks` option (beta) that re-runs a refused request on another model; leadgen doesn't use it. | Fine for opening lines, where a template is a good fallback. Passing it through `writer.llm.extra_body` / `extra_headers` is possible in principle but **untested**, and the cost estimate would still price the configured model. |
| **Built-in AI prices can go stale** | `LLM_PRICES` in `leadgen/usage.py` (table in [2.11](#211-usage-metering-the-budget-and-prices-leadgenusagepy)). The code comment says these are "Anthropic first-party API list prices, cached 2026-06". An unknown model is priced at 10 / 50 so cost caps err on the safe side. | Override with `usage.llm_price_per_mtok`. The default model is `claude-opus-5`; the delivery playbook suggests `claude-haiku-4-5` for opening lines. |
| **Google Sheets and doctor exceptions to "all HTTP via `Adapter.http`"** | Sheets goes through `gspread`. The doctor deliberately uses the un-metered `ctx.http`, so its free checks are never blocked by a budget. | Known and intentional. |
| **`leadgen stats -p <base playbook>` doesn't show client deliveries** | Client deliveries run under the playbook name `client-<name>`. For example, `stats -p playbooks/demo-delivery.yaml` showed 0 leads, while `stats` without `-p` showed 13. | Use `leadgen clients` for delivery history, or `leadgen stats` without `-p`. |

**Documentation drift: every known case, consolidated from all sections**

Each item below was re-checked in the files on 2026-09-25. In every case **the code is right and the text is stale**, unless the item says otherwise. None of them was changed while writing this document.

| # | Where the text is | What it says | What is actually true | Fix |
|---|---|---|---|---|
| 1 | README "Freshness" (around line 429); the comments on Adzuna `max_days_old` and TheirStack `max_age_days` in `playbooks/recruitment-delivery.yaml` (around lines 178-181 and 233-234); the `freshness_days` comment in `clients/_template.yaml` | A client file **can't** widen a source's own date window ("widen it there too"). | Since commit `03ec275`, `client_playbook` (`leadgen/delivery/client.py`) **does** widen Adzuna `max_days_old` and TheirStack `max_age_days` up to the client's `freshness_days`. It only widens, never narrows, and `tests/test_delivery_playbooks.py` covers it. Cost impact: TheirStack charges per job returned, so a long freshness window fetches more paid jobs (see 4.5.2). | Update the three texts. |
| 2 | README "Commands" | Lists `leadgen mark` under "Outbound mode only". | `mark` is **not** gated: on a delivery playbook it runs and only changes a stored lead's stage (see 6.4). Harmless. | Move `mark` to the "Reports" group, or gate it. |
| 3 | `playbooks/templates/generic.yaml` (around line 267) | An empty `model` means the provider's default, `claude-sonnet-5` for Anthropic. | The Anthropic client's default is `claude-opus-5` (`default_model` in `leadgen/llm/anthropic.py`). | Fix the comment, or change the default on purpose. |
| 4 | `playbooks/templates/generic.yaml` (around line 268) | `claude-opus-5-5` is "best, costs more". | `LLM_PRICES` lists `claude-opus-5-5` at 4 / 20 USD per million input / output tokens, **cheaper** than `claude-opus-5` at 5 / 25. | Fix the comment. |
| 5 | `PLAN.md` section 1 | "We don't email, call or message anyone, for ourselves or for our clients." | `PLAN.md` section 5, the README and `playbooks/my-agency.yaml` describe using outbound mode for the owner's **own** prospecting. The product (client deliveries) never does outreach; the "for ourselves" part is a business choice (see 1.5). | Reword section 1 of `PLAN.md`. |
| 6 | The docstring of `leadgen/sources/adzuna.py` | `countries` defaults to `["us"]`. | The code raises an error when `countries` is missing ("adzuna: set 'countries' (Adzuna searches one country per request, ..."). The shipped playbook sets it and marks it REQUIRED. | Fix the docstring. |
| 7 | `playbooks/my-agency.yaml` `offer` and `writer.extra_instructions` (content, not documentation) | Pitch "outbound lead generation and AI automation" and a new sales hire starting with "meetings already booked". | The business now sells the Hiring Signal Report. | Rewrite before any real use (see 4.13 and 5.9.3). |

### 6.8 Project history

All 19 commits were made between 2026-09-24 13:18 UTC and 2026-09-25 04:48 UTC. The project
went through five phases. Bug counts below are **only what the commit messages state**. Two
commits give a number; the others list fixes without counting them.

| # | Phase | Commits | What happened | Size (from `git log --shortstat`) |
|---|---|---|---|---|
| 0 | **Plan** | `75aa587` | v1 build plan for a niche-agnostic lead-gen engine (`PLAN.md`) | 1 file |
| 1 | **Outbound engine build** | `a735dce`, `fb73342` | Foundation (models, playbook, store, pipeline, adapter interfaces), then every module: sources, enrichment, verification, scoring, AI writer, replies, exporters, CLI, webhook server, playbooks | +2,746 lines, then +25,913 lines in 70 files |
| 2 | **Adversarial reviews and hardening** (code reviews that deliberately hunt for bugs) | `750d52b`, `e77b009`, `7b510fe`, `d8e29fa` | Foundation fixes (company cooldown, export ordering, sticky LOST, LLM options, reply dedupe). **"Fix 57 review findings"**: secrets scrubbed from errors and logs, no double contact, deliverability rules, reply misclassification fixes, source parsing, writer guardrails. No AI spend on leads that can't be handed over. A docs audit that checked the README command by command from a fresh clone, plus 9 listed code fixes. | `e77b009`: 59 files, +4,233 / -441 |
| 3 | **Delivery pivot** (the business changed from "do outreach" to "sell lead files") | `345c758`, `81008c2`, `2941bac`, `bc53972`, `725be5f`, `7487b83`, `a3927a9`, `f889816`, `3dc72f9`, `7208bfd` | Run modes (delivery the default), usage meter and `--budget`, pipeline hooks, LinkedIn / Indeed flagged "use at own risk", default model restored to `claude-opus-5`. Then the delivery contract (`docs/DELIVERY.md`), client files and ledger, report formats and opening lines, `deliver()` and QA, `leadgen doctor` and AI token metering, delivery playbooks and demo data, the CLI commands, docs and end-to-end tests, small fixes. | about 15,700 lines added across the 10 commits |
| 4 | **Delivery review** | `6fa6e29`, `03ec275` | **"Fix 29 review findings in the delivery layer"**: ledger identity, no overwrites, preview files kept out of the send folder, NaN/inf cost caps, the doctor never sends `OPENAI_API_KEY` to a non-OpenAI host, and more. Then foundation fixes: email honesty for provider-flagged guesses, do-not-lists after enrichment, budget edge cases (keep what was paid for, never re-send a timed-out POST), an append-only delivery-run count, source windows widened, a formula-injection guard on `rejected.csv`. | `6fa6e29`: 25 files, +1,830 / -177 |

**Bug-fix count as stated in the commit messages:** at least **86 numbered review findings
fixed** (57 in `e77b009` + 29 in `6fa6e29`). There are further unnumbered fix batches in
`750d52b`, `d8e29fa`, `7208bfd` and `03ec275`.

### 6.9 Roadmap

The priorities are a suggestion. "Business" items are decisions and actions for the owner;
"Technical" items are code or setup work. Any money figure here is an **assumption, not
market research**. Now / Next / Later line up roughly with Stage 0 / Stages 0-1 / Stages 1-3 in section 3, and all the stack changes are in priority order in [3.18](#318-priority-change-list).

| When | Type | Item | Why | Done when |
|---|---|---|---|---|
| **Now** | Business | Pick one niche and one region. Set Adzuna `countries` and `queries` in `playbooks/recruitment-delivery.yaml`, or a copy of it. | The search words decide what is fetched (see 6.7). | A real rehearsal returns relevant jobs. |
| **Now** | Technical | First live run: free Adzuna key, then `doctor`, dry run, rehearsal on a database copy, spot-check (6.6). | Nothing has been tested live. | Adzuna rows look right; any mapping bug is fixed with a new `FakeHttp` test. |
| **Now** | Business | Make a sample report for prospects: a real delivery for a made-up client in their niche (`leadgen clients new sample-finance`), or `leadgen demo -p PLAYBOOK` after a `leadgen run`. | You need something to show. | One HTML plus Excel file you are happy to send. |
| **Now** | Business | Rewrite `my-agency.yaml`'s `offer` and `writer.extra_instructions` to pitch the report. Decide whether to prospect with outbound mode (needs paid keys; always use `--budget`) or by hand. | It still pitches the old service. | The offer text in a dry run's `opportunities.csv` (template writer) reads correctly. |
| **Now** | Business | Read the data-provider terms (reselling) and the privacy rules for your market (`PLAN.md` §6). | Legal risk. Not legal advice. | You know what you may sell. |
| **Now** | Technical | Set up a backup routine for `data/*.db`. | Losing it means re-delivering old leads. | A dated copy exists after every delivery. |
| **Now** | Technical | Fix the docs drift in 6.7 (source windows; `mark`). | Docs must match the code. | README and playbook comments updated; tests still pass. |
| **Next** (first 1-3 clients) | Business | Offer a pilot (2-4 weekly reports, one niche, one region; `PLAN.md` §2). | A testimonial plus proof of quality. | A first paying client. |
| **Next** | Technical | Switch on paid tools one at a time, each with `--budget 20` first: Hunter, then a paid checker, then Apollo or TheirStack. Fill `usage.cost_per_call` from your own plan. | Better names and more `verified` emails, at a known cost. | Usage lines match the provider dashboard. |
| **Next** | Technical | Cron per client, alert on a non-zero exit code, a weekly `leadgen doctor` (README "Weekly automation"). | Delivery day runs without surprises. | A week passes with no manual run. |
| **Next** | Technical | Add CI: a GitHub Actions workflow running `pytest -q` on Python 3.9 and a current Python. | There is no CI; 3.9 support is easy to break by accident. | A green check on every push. |
| **Next** | Technical | Test seeding job history before a client's first delivery (repost detection). | The first delivery can include reposts. | Documented result. |
| **Next** | Business | Decide each client's `dedupe` (company once vs new jobs allowed) and `leads_per_week`, from the QA "already delivered" share. | Volume drains under company-level dedupe. | A per-plan default is written down. |
| **Next** | Business | Track the numbers in `PLAN.md` §8: delivered vs target, verified rate, "already delivered" share, cost per delivery, client outcomes. | That is what renews subscriptions. | A monthly review habit. |
| **Later** | Technical | Derive source search words from the client's `roles`. | Removes one playbook copy per niche. | A client in a new niche works on the default playbook. |
| **Later** | Technical | Signal-neutral report wording and file names. | Only if you sell non-hiring signals. | "Hiring" is no longer hard-coded. |
| **Later** | Technical | AI: decide between the official SDK and raw HTTP (keep metering and `--budget`; mind the SDK 1.x Python 3.10+ requirement). Consider Anthropic's server-side refusal fallbacks if AI output becomes central. | Maintenance and new API features. | A decision recorded in `docs/ARCHITECTURE.md`. |
| **Later** | Technical | Live-test Google Sheets (`pip install -e ".[sheets]"`) and Slack alerts. | Conveniences for clients and for you. | One real push each. |
| **Later** | Technical | A "deliver every client" command; a client portal; billing; CRM sync. All listed as "Not built" in `PLAN.md`. | Scale beyond a handful of clients. | Only when manual work becomes the bottleneck. |
| **Later** | Technical | Move off single-user SQLite, only if several people or processes must run it at once. | Concurrency. | Not needed for a solo operator. |
| **Later** | Business | Growth plan with paid tools, and an "exclusive niche + region" add-on (`PLAN.md` §2, rough ideas). | Higher price per client. | Priced from your measured cost per delivery. |

**Spending a USD 100-200 starting budget (a rough suggestion, not researched; the item-by-item split is in [5.8](#58-budget-allocation-for-usd-100-200) and the staged tool plan in [4.9.6](#496-a-starting-plan-for-a-usd-100-200-budget-assumptions)):** the
default delivery path costs nothing: Adzuna, the job boards, CSV lists, pattern guesses and
the basic check. It is reasonable to run the first pilot deliveries on that free path. Buy
the first paid tool only once a client has agreed to pay, and keep most of the budget in
reserve until then. Every paid run should carry a `--budget`. AI opening lines are capped
per delivery by `max_cost_usd` (default USD 0.50). No provider prices are given here on
purpose: they depend on your plan, so read them from the provider and put them in
`usage.cost_per_call`.

### 6.10 How to continue this project (for an AI assistant or developer)

**Start here: reading order**

1. `README.md`: what the user sees and every command. Tests check that its command table
   and internal links stay correct.
2. `PLAN.md`: the business, the weekly workflow, the risks.
3. `docs/ARCHITECTURE.md`: modes, the pipeline stage by stage, the store, adapter contracts.
4. `docs/DELIVERY.md`: the **binding** contract for `leadgen/delivery/`.
5. The code, in this order: `leadgen/modes.py` → `pipeline.py` → `delivery/run.py` →
   `delivery/rows.py` → `delivery/ledger.py` → `delivery/client.py` → `usage.py` →
   `registry.py` → `context.py` → `http.py`. Each module's docstring lists the playbook keys
   it reads. `leadgen/playbook.py` `DEFAULTS` holds every default, and
   `playbooks/templates/generic.yaml` explains every setting in plain English.
6. Tests: `tests/conftest.py`, `tests/fakes.py`, then `tests/test_e2e_delivery.py` and
   `tests/test_modes.py`, which state the product's promises as tests.

**Setup and running**

```bash
cd Lead-Gen-
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"          # add ".[dev,sheets]" for Google Sheets
pytest -q                        # 2073 tests, about 25 s; no network needed
leadgen deliver --client demo-client --db /tmp/x/demo.db --out /tmp/x/{client}/{date}   # offline smoke test
```

- Run `leadgen` from the repo root: `clients/`, `playbooks/`, `.env`, `data/` and
  `deliveries/` are found relative to it.
- Point `--db` and `--out` at scratch paths when experimenting, so real ledgers and folders
  stay untouched. (A delivery with a non-default `--db` prints a `Careful:` line. That is
  expected.)
- `-v` / `-vv` shows more logging and full tracebacks.
- Exit codes: `0` ok, `1` ran but found a problem (for example nothing delivered),
  `2` usage or configuration error.

**Invariants that must never break**

| Invariant | Enforced in | Guarded by (examples) |
|---|---|---|
| **Delivery mode never writes emails, hands leads over or sends anything.** | `modes.require_outbound` (inside `replies.handle_reply` and `server.make_server`); `Pipeline` checks `is_outbound(ctx)` before the WRITE and hand-over stages; the CLI's `outbound_gate` refuses `replies` / `serve` / `followups` before opening a database; `client_playbook` forces `mode: delivery` and strips hand-over exporters | `test_modes.py::test_delivery_pipeline_never_writes_or_hands_over`, `::test_delivery_mode_refuses_replies_and_server`, `test_e2e_delivery.py::test_outbound_only_commands_refuse_in_delivery_mode` |
| **Email-label honesty.** Exactly four labels. A guess (pattern source, `email_guessed`, a candidate address, or a provider flag such as "guessed" / "extrapolated") is **never** `verified`, whatever a checker says. | `rows.email_label`, `rows.is_guessed` | `test_e2e_delivery.py::test_email_labels_are_honest`, `::test_guessed_email_is_never_verified_even_when_a_paid_checker_says_valid` |
| **The ledger never re-delivers** a company, job or contact to the same client (unless `dedupe` / `redelivery_days` allow it). Held-back leads and dry runs record nothing. A delivery folder is never overwritten. | `delivery/ledger.py` (`LedgerHooks`, `Ledger.record`, item keys), `delivery/run.py` | `test_e2e_delivery.py::test_next_delivery_never_repeats_a_company_job_or_contact`, `::test_dry_run_previews_without_recording_anything`, `test_ledger.py` |
| **Budget before network.** A paid request over the cap raises `BudgetExceeded` *before* the inner HTTP call. Paid types are in `registry.PAID`; plugins use `register(..., paid=True)`. Do-not-lists and the ledger drop companies before any paid lookup. | `usage.UsageMeter.before_request`, `MeteredHttp`, `PipelineHooks` | `test_modes.py::test_budget_blocks_paid_requests_before_the_network`, `::test_hooks_drop_companies_before_enrichment` |
| **Secrets are redacted.** Keys are read lazily with `Adapter.secret()` (never in `__init__`); errors and logged URLs go through `http.redact` / `safe_url`; the doctor masks keys; the webhook server masks `?token=`. | `leadgen/http.py`, `leadgen/context.py`, `leadgen/doctor.py`, `leadgen/server.py` | `test_foundation.py::test_safe_url_and_redact`, `::test_http_network_error_never_leaks_query_keys`, `test_doctor.py::test_keys_never_leak_even_when_providers_echo_them` |
| **Free by default.** Shipped delivery playbooks use no paid, risky or hand-over adapter. | the playbooks | `test_delivery_playbooks.py::test_delivery_playbooks_use_no_risky_or_handover_adapter`, `::test_recruitment_delivery_is_zero_budget_as_shipped` |

**Conventions**

- **Adapters via the registry.** A built-in adapter goes into `registry._REGISTRY`; a
  plugin uses `registry.register(kind, type, "module:Class")`. If it costs money per
  request or result, add it to `registry.PAID` (or pass `paid=True`). If it scrapes a site
  whose terms forbid it, give it a `risk_note`. Set `env_key` for its default key.
  `offline = True` only if it never touches the network. Hand-over exporters set
  `scope = "outbound"`. Add a free credential check to `doctor.CHECKS` where one exists.
- **All HTTP goes through `Adapter.http`**, which is metered, retries 429 / 5xx and
  redacts secrets. Never import `requests` in an adapter. The known exceptions are
  `gspread` (Sheets) and the doctor's deliberately un-metered `ctx.http`. `cli.make_http()`
  is the single HTTP factory.
- **Tests use `FakeHttp`** (`tests/fakes.py`) with canned responses in the provider's
  documented shape, and `FakeLLM` for AI. CLI tests monkeypatch `cli.make_http`. No test
  may reach the real network (the webhook-server tests use `127.0.0.1` only).
- **`conftest.py` defaults to outbound.** `build_ctx` / `make_ctx` build a playbook with
  `mode: outbound` because many module tests exercise outbound features. Delivery tests
  must pass `mode="delivery"`. `test_modes.py` pins the real engine default (delivery).
  `TODAY` is fixed at 2026-09-24, and the store is in memory.
- **New shipped playbooks** must be added to `SHIPPED_PLAYBOOKS` in `tests/conftest.py`,
  which is a fixed list, not a glob.
- **Docs are tested.** `test_e2e_delivery.py` checks that every documented command and flag
  exists, that the README command table and internal links are right, that `.env.example`
  lists every variable the code and playbooks read, and that client data stays out of git.
  A new command, flag or env var needs README and `.env.example` updates, or the suite fails.
- **Style:** no `print` in adapters (use `self.log`); parse responses defensively (`.get`,
  `utils.get_path`); config problems become friendly `error:` lines with exit code 2.
- **Commit style:** a short imperative subject ("Add ...", "Fix 29 review findings in the
  delivery layer"), optionally a bullet body grouped by area, then the trailers
  `Co-Authored-By: ...` and `Claude-Session: ...` used by every commit so far. Never commit
  `.env`, `data/*.db`, `deliveries/` or `output/` (they are in `.gitignore`).

**A safe change, step by step**

1. Read the contract for the area (`docs/DELIVERY.md` or `docs/ARCHITECTURE.md`) and the
   module docstring.
2. Write a failing test first, with `FakeHttp` / `FakeLLM` and `make_ctx`. Pass
   `mode="delivery"` when relevant.
3. Change the code, keeping every invariant above.
4. `pytest -q`. All 2,073+ tests must pass. If you touch syntax or typing, run it on
   Python 3.9 too.
5. Update `README.md`, `docs/`, `playbooks/templates/generic.yaml` and `.env.example`
   where needed.
6. Smoke-test the demo into a scratch `--db` / `--out`, then commit in the style above.

---

## Appendix A. File map (quick reference)

Paths are relative to the repository root. "In git?" says whether git tracks the path today (checked with `git check-ignore` on 2026-09-25). The engine package is described module by module in [2.3](#23-repository-layout).

| Path | What it is | In git? | Details |
|---|---|---|---|
| `README.md` | The owner guide (1,136 lines): what the client gets, setup, every command, automation, troubleshooting. Tests check its command table and internal links. | Yes | [D](#appendix-d-other-documents-in-the-repository) |
| `PLAN.md` | The business plan (134 lines): the business, rough pricing ideas, weekly workflow, what's built, what's switched off, risks, setup checklist, numbers to watch. | Yes | [D](#appendix-d-other-documents-in-the-repository), [5](#5-business-and-agency-plan) |
| `CONTEXT.md` | This document. | New file | - |
| `docs/ARCHITECTURE.md` | Module contracts (developer reference). | Yes | [2](#2-technical-architecture) |
| `docs/DELIVERY.md` | The **binding** contract of the client-delivery layer. | Yes | [2.9](#29-the-delivery-layer-leadgendelivery) |
| `pyproject.toml` | Package metadata (`leadgen` 0.1.0, `requires-python = ">=3.9"`), dependencies, extras `[dev]` and `[sheets]`, the `leadgen` console script, pytest config. | Yes | [3.1](#31-language-and-runtime) |
| `requirements.txt` | `requests`, `PyYAML`, `openpyxl`, `pytest` (no `gspread`). | Yes | [3.1](#31-language-and-runtime) |
| `.env.example` | Every environment variable (28, grouped delivery-first). Copy it to `.env`. | Yes | [4.3](#43-the-env-file-every-variable) |
| `.env` | Your API keys and sender details (you create it). Never commit it. | No (ignored) | [4.3](#43-the-env-file-every-variable) |
| `.gitignore` | Ignores `.env`, `*.db`, `data/*.db`, `output/`, `deliveries/`, caches and build files. Does **not** ignore `clients/*.yaml`, `data/imports/` or `logs/`. | Yes | [3.4](#34-configuration) |
| `clients/_template.yaml` | Every client-file key with plain-English comments; copied by `leadgen clients new`. Files starting with `_` are never treated as clients. | Yes | [4.5.2](#452-every-client-file-key) |
| `clients/demo-client.yaml` | The made-up demo agency, Northstar Finance Recruiting, on `playbooks/demo-delivery.yaml`. | Yes | [1.3](#13-what-a-client-receives-a-walk-through-the-real-demo-output) |
| `clients/<name>.yaml` | One real client's file. Its short name is the client's key in the ledger: never rename it after the first delivery. | **Not ignored**: keep the repository private, or ignore real client files | [4.5](#45-setting-up-a-real-client), [3.4](#34-configuration) |
| `playbooks/recruitment-delivery.yaml` | The real delivery playbook: `mode: delivery`, only free tools switched on, finance roles, Adzuna `countries: [us]`, database `data/leadgen.db`. The default base of every client file. | Yes | [4.5.3](#453-point-the-base-playbook-at-your-niche-and-market) |
| `playbooks/demo-delivery.yaml` | Offline demo delivery (CSV sources and CSV contacts; database `data/demo-delivery.db`). | Yes | [1.3](#13-what-a-client-receives-a-walk-through-the-real-demo-output) |
| `playbooks/demo-offline.yaml` | Offline demo of outbound mode (database `data/demo.db`). | Yes | [4.13](#413-outbound-mode-operations-brief) |
| `playbooks/my-agency.yaml` | Outbound playbook for finding the owner's **own** clients: paid tools, old offer text, database `data/leadgen.db`. | Yes | [1.5](#15-the-two-modes-delivery-on-and-outbound-off), [4.13](#413-outbound-mode-operations-brief), [5.9.3](#593-using-playbooksmy-agencyyaml-optional-needs-edits-first) |
| `playbooks/templates/generic.yaml` | Every playbook setting explained in plain English (`mode: delivery`); the default template for `leadgen init`. | Yes | [2.6](#26-configuration-playbooks-leadgenplaybookpy) |
| `playbooks/templates/recruitment.yaml`, `agency-outreach.yaml`, `saas-funding.yaml`, `local-business.yaml` | Outbound example playbooks: recruitment; agency outreach; funding signals via Apollo; Google Maps reviews via Apify. | Yes | [1.6](#16-what-niche-agnostic-means-here-precisely) |
| `examples/data/` | Made-up sample data (domains end in `-demo.com`): `demo_jobs.csv` (40 job rows), `demo_delivery_contacts.csv`, `demo_signals.csv`, `demo_contacts.csv`, `demo_replies.csv`. | Yes | [1.3](#13-what-a-client-receives-a-walk-through-the-real-demo-output) |
| `leadgen/` | The engine package: 73 files, about 25,900 lines. | Yes | [2.3](#23-repository-layout) |
| `leadgen/cli.py` | Every `leadgen` command; `make_http()` (the one HTTP factory); `.env` loading; plugin loading; the outbound gate. | Yes | [2.16](#216-command-line-surface-leadgenclipy), [4.12](#412-command-reference) |
| `leadgen/modes.py` | `delivery` / `outbound`, `require_outbound`. | Yes | [2.7](#27-modes-and-the-outbound-guard-leadgenmodespy) |
| `leadgen/playbook.py` | `DEFAULTS` (every setting's default), `${ENV}` expansion, validation. | Yes | [2.6](#26-configuration-playbooks-leadgenplaybookpy) |
| `leadgen/pipeline.py` | The pipeline, stages 1-9. | Yes | [2.8](#28-the-pipeline-stage-by-stage-leadgenpipelinepy) |
| `leadgen/delivery/run.py` | `deliver()` and `select_leads()`. | Yes | [2.9](#29-the-delivery-layer-leadgendelivery) |
| `leadgen/delivery/client.py` | Client files and `client_playbook()`. | Yes | [2.9](#29-the-delivery-layer-leadgendelivery), [4.5.2](#452-every-client-file-key) |
| `leadgen/delivery/ledger.py` | The ledger and the per-client do-not-list. | Yes | [2.9](#29-the-delivery-layer-leadgendelivery) |
| `leadgen/delivery/rows.py` | The 18 (+1) client columns and the four email labels. | Yes | [1.3.1](#131-the-csv-the-main-product), [2.9](#29-the-delivery-layer-leadgendelivery) |
| `leadgen/delivery/formats.py`, `opening.py`, `qa.py` | The CSV / Excel / HTML / Google Sheets writers; suggested opening lines and the AI cost cap; the QA summary. | Yes | [2.9](#29-the-delivery-layer-leadgendelivery), [4.7](#47-handling-the-qa-summary) |
| `leadgen/usage.py` | Metering, the paid-lookup budget, `LLM_PRICES`. | Yes | [2.11](#211-usage-metering-the-budget-and-prices-leadgenusagepy) |
| `leadgen/registry.py` | The adapter registry, the `PAID` set, risk notes, plugins. | Yes | [2.13](#213-adapter-registry-and-plugins-leadgenregistrypy) |
| `leadgen/doctor.py` | Free key checks. | Yes | [2.14](#214-leadgen-doctor-leadgendoctorpy) |
| `leadgen/store.py` | The SQLite store. | Yes | [2.10](#210-storage-leadgenstorepy) |
| `leadgen/http.py` | HTTP client: retries, backoff, redaction. | Yes | [2.12](#212-http-layer-leadgenhttppy) |
| `leadgen/sources/`, `enrich/`, `verify/`, `llm/`, `writer/`, `outbound/`, `notify/` | Adapters, one folder per kind. | Yes | [2.3](#23-repository-layout), [2.13](#213-adapter-registry-and-plugins-leadgenregistrypy) |
| `tests/` | 32 test files plus `conftest.py`, `fakes.py` and `__init__.py`; 2,073 tests. | Yes | [2.18](#218-testing-strategy-tests) |
| `tests/conftest.py`, `tests/fakes.py` | Fixtures (outbound mode by default, `TODAY = 2026-09-24`, in-memory store); `FakeHttp` and `FakeLLM`. | Yes | [2.18](#218-testing-strategy-tests), [6.10](#610-how-to-continue-this-project-for-an-ai-assistant-or-developer) |
| `tests/test_e2e_delivery.py`, `tests/test_modes.py` | The product's promises stated as tests. | Yes | [6.10](#610-how-to-continue-this-project-for-an-ai-assistant-or-developer) |
| `data/*.db` | SQLite databases (store + ledger), created at run time: `data/leadgen.db` (real clients and `my-agency.yaml`), `data/demo-delivery.db`, `data/demo.db`. **Back them up.** | No (ignored) | [4.11](#411-backing-up-the-ledger-database) |
| `data/imports/` | Your own CSV lists (for example `jobs.csv`, named in the playbook's disabled `csv` source). May hold personal data. | **Not ignored** | [3.4](#34-configuration) |
| `deliveries/<client>/<date>/` | Delivered files. The `_internal/` folder inside is yours only. | No (ignored; personal data) | [1.3](#13-what-a-client-receives-a-walk-through-the-real-demo-output), [4.5.7](#457-what-to-send-the-client-and-what-not) |
| `output/<playbook>/<run id>/` | Output of `leadgen run` (`leadgen replies` writes `replies_classified.csv` in `output/<playbook>/`); also used for rehearsals. | No (ignored) | [2.8](#28-the-pipeline-stage-by-stage-leadgenpipelinepy), [4.5.5](#455-preview-then-a-rehearsal-on-a-copy) |
| `logs/`, `backups/` | Created by the automation scripts, if you use them. | `logs/` **not ignored**; `backups/*.db` ignored through the `*.db` rule | [4.10](#410-automation) |
| `leadgen.egg-info/`, `.pytest_cache/`, `.ruff_cache/`, `__pycache__/` | Build metadata and tool caches. | Not tracked | - |

## Appendix B. Command cheat sheet

Every command, one line each. Run them from the repository root. `NAME` is a client's short name, `PB` a playbook path. Flags are the ones checked against `leadgen <command> --help` (full reference: [4.12](#412-command-reference)). Global options work before or after the command: `-p PATH`, `--db PATH`, `-v` / `-vv`, `--env-file PATH`.

```bash
# --- Install (once) ---
python3 -m venv .venv && source .venv/bin/activate   # Windows: py -m venv .venv, then .venv\Scripts\activate
pip install -e .                                     # the leadgen command
pip install -e ".[dev]"                              # + pytest
pip install -e ".[sheets]"                           # + gspread, only for Google Sheets pushes
cp .env.example .env                                 # then fill ADZUNA_APP_ID, ADZUNA_APP_KEY, SENDER_NAME, SENDER_EMAIL, SENDER_WEBSITE
leadgen --version                                    # leadgen 0.1.0
leadgen adapters                                     # every adapter type: offline / network / paid, key, notes

# --- The offline demo (free, no keys) ---
leadgen deliver --client demo-client --dry-run       # PREVIEW files, nothing recorded
leadgen deliver --client demo-client                 # a real demo delivery (database data/demo-delivery.db)

# --- A new client ---
leadgen clients new NAME                             # copies clients/_template.yaml to clients/NAME.yaml (never overwrites)
leadgen clients                                      # every client: leads/week, deliveries, last delivery, total delivered
leadgen validate -p playbooks/recruitment-delivery.yaml   # offline checklist: mode, adapters, config, keys
leadgen doctor --client NAME                         # one free call per API key (never a paid lookup)
leadgen doctor --client NAME --dry-run               # only checks that the keys are set; contacts nobody
leadgen deliver --client NAME --dry-run              # preview (empty on the default, network-only playbook)
cp data/leadgen.db data/rehearsal.db                 # rehearsal on a COPY of the database...
leadgen deliver --client NAME --db data/rehearsal.db --out output/rehearsal/{client}/{date} --budget 20   # ...never send its files

# --- Every week ---
leadgen deliver --client NAME                        # the real delivery: files + QA summary + ledger record
leadgen deliver --client NAME --budget 20            # the same, with at most 20 paid lookups

# --- Do-not-lists ---
leadgen suppress add person@example.com --reason "asked not to be listed"    # global list
leadgen suppress add "Example Corp" --kind company                           # company names need --kind company
leadgen suppress add bigclient.example --client NAME --reason "their client" # one client's list
leadgen suppress add --file list.csv --kind company --client NAME            # bulk import
leadgen suppress list [--client NAME]
leadgen suppress remove VALUE [--client NAME]

# --- Pipeline runs and reports (no client, no ledger) ---
leadgen init my-playbook --template generic          # new playbook from playbooks/templates/ (never overwrites)
leadgen run -p PB [--dry-run] [--limit N] [--budget N] [--out DIR]
leadgen leads [-p PB] [--run latest|all|RUN_ID] [--tier hot|normal|skip] [--limit 20]
leadgen demo -p PB [--run latest] [--top 5] [--prospect "Name"] [--no-mask] [--out DIR]
leadgen stats [-p PB] [--since YYYY-MM-DD] [--runs 10]
leadgen mark [-p PB] (--email EMAIL | --lead ID) --stage booked [--note "call booked"] [--force]

# --- Outbound mode only (your own prospecting; refused on delivery-mode playbooks) ---
leadgen validate -p playbooks/my-agency.yaml
leadgen doctor -p playbooks/my-agency.yaml
leadgen run -p playbooks/my-agency.yaml --dry-run
leadgen run -p playbooks/my-agency.yaml --limit 10 --budget 100
leadgen replies -p playbooks/my-agency.yaml --file replies.csv [--out DIR]
leadgen followups -p playbooks/my-agency.yaml [--days 30] [--done ID]
leadgen serve -p playbooks/my-agency.yaml [--host 127.0.0.1] [--port 8787] [--token TOKEN]

# --- Backup and restore check ---
mkdir -p backups
python -c "import sqlite3,datetime; s=sqlite3.connect('data/leadgen.db'); d=sqlite3.connect(f'backups/leadgen-{datetime.date.today()}.db'); s.backup(d); d.close(); s.close()"
leadgen clients --db backups/leadgen-YYYY-MM-DD.db   # shows the delivery history stored in a backup

# --- Developer ---
pytest -q                                            # 2,073 tests, no network
leadgen deliver --client demo-client --db /tmp/x/demo.db --out /tmp/x/{client}/{date}   # offline smoke test on scratch paths
leadgen -vv deliver --client NAME                    # debug logging and full tracebacks
```

Exit codes everywhere: `0` ok, `1` the command ran but found a problem, `2` usage or configuration error, `130` interrupted with Ctrl+C.

## Appendix C. Glossary

One glossary for the whole document, merged from every section. "Where it lives" names the code, file or section with the details.

| Term | Plain-English meaning in this project | Where it lives |
|---|---|---|
| **Adapter** | A plug-in class for one outside service or file type, chosen in a playbook with `type:`. Kinds: source, finder, verifier, llm, writer, exporter, notifier. `leadgen adapters` lists them all as offline / network / network (paid). | `registry.py`; `context.py` `Adapter` |
| **ATS (applicant tracking system)** | The software a company uses to post jobs and manage applicants. Greenhouse, Lever and Ashby publish public job boards that leadgen can read for free. Recruitment agencies also run their own systems (for example Bullhorn); pushing leads into one is a future idea, not built. | `sources/ats.py`; 3.10 |
| **Base playbook** | The playbook a client file builds on (its `playbook:` line; default `playbooks/recruitment-delivery.yaml`). | client file |
| **BD (business development)** | Finding new client companies. At a small agency the owner usually does it, and it is the work the report replaces. | 5.2 |
| **BOM (byte-order mark)** | A hidden marker at the start of the CSV that makes Excel show accented characters correctly. | `delivery/formats.py` |
| **Budget** | The **maximum number of paid lookups** in one run. It is a count, not dollars. The first one set wins: `--budget N`, then the client's `budget.max_paid_lookups`, then the playbook's `usage.max_paid_lookups`. In the playbook and with `--budget 0`, `0` means no cap at all. In a client file, `0` means "no cap of its own", so the playbook's cap still applies. Once the cap is reached, paid requests are refused before they hit the network. (AI opening lines have a separate dollar cap: `opening_line.max_cost_usd`.) | `usage.py` `UsageMeter`, `BudgetExceeded` |
| **Canned responses** | Made-up answers in the shape a provider documents, fed to the code by the tests instead of the real service. Every connector has only been tested this way. | `tests/fakes.py` |
| **Careful line** | The warning printed after a real delivery with leads that used a `--db` other than the client playbook's database. It stops you sending files from a rehearsal. | `cli.py` `_print_delivery`; 4.5.5 |
| **Catch-all (mail server)** | A mail server that accepts every address, so no checker can confirm a mailbox. Such addresses are labelled `risky`. | `verify/*`; `delivery/rows.py` |
| **Churn** | Clients lost in a month divided by the clients at the start of that month. | 5.15 |
| **CI (continuous integration)** | Tests that run automatically on every change, for example with GitHub Actions. Not set up in this repository (there is no `.github/` folder). | 3.15 |
| **Client file** | One YAML per client (`clients/<name>.yaml`) holding *what that client wants*: roles, locations, size, exclusions, leads per week, freshness, email policy, formats, branding. Typos are caught with a "did you mean". | `clients/`; `delivery/client.py`; `clients/_template.yaml` explains every key |
| **Container (Docker)** | A package of the code plus its exact environment, so it runs the same on any machine. Not used yet. | 3.1 |
| **Contingency agency** | A recruitment agency that is paid only when a candidate it introduced is hired. The fee is commonly quoted as roughly 15-25% of first-year salary (an assumption, not researched data). | 5.2 |
| **`cost_per_call`** | Your plan's price per request for a paid provider, entered by you under `usage.cost_per_call` so the usage lines can estimate cost. The engine has no built-in data-provider prices. | playbook `usage:`; 4.9.3 |
| **CRM (customer relationship management system)** | Where agencies track contacts and deals. The CSV imports into any CRM; nothing pushes to one directly. | 3.10 |
| **cron / Task Scheduler** | The built-in task timers of Linux / macOS (cron) and Windows (Task Scheduler), used to run the weekly delivery automatically. | README "Weekly automation"; 4.10 |
| **Decision-maker** | The person most likely to own the hire, chosen by matching job titles against the client's `buyer_titles` (best first). One per company in a delivery. | `contacts.py`; column "Decision-maker" |
| **Dedupe** | The client-file setting that says what must never repeat for a client: `company`, `job`, `contact` (all three by default). | client file `dedupe`; 4.5.2 |
| **Doctor** | `leadgen doctor`: checks every API key a playbook or client uses with one call to a free endpoint each. Never a paid lookup, never blocked by `--budget`. | `doctor.py`; 2.14, 4.5.4 |
| **Dry run / preview** | `--dry-run`: only offline adapters run, files are named `...-PREVIEW`, nothing is recorded in the ledger, no Google Sheets push, AI falls back to templates. With the default live playbook (all network sources) a preview is empty; it only proves the settings load. | `delivery/run.py`; `context.py` |
| **Editable install** | `pip install -e .`: installs the `leadgen` command so that code changes take effect without reinstalling. | 4.2 |
| **Enrichment** | Adding people and emails to a company (the finders, then the email checker). | `contacts.py`, `enrich/*`, `verify/*` |
| **Exit code** | The number a command returns: `0` ok; `1` the command ran but found a problem (for example nothing delivered); `2` usage or configuration error; `130` interrupted. | `cli.py` |
| **Exporter** | An adapter that writes the pipeline's leads somewhere. **Review exporters** (`csv`, `json`, `gsheets`) run in both modes and write internal files such as `opportunities.csv`. **Hand-over exporters** (`instantly`, `instantly_csv`, `smartlead`, `smartlead_csv`, `webhook`) are outbound-only. The client's CSV / Excel / HTML are **not** exporters: they are written by `delivery/formats.py`. | `outbound/*` |
| **FakeHttp / FakeLLM** | The test doubles that answer HTTP and AI calls with canned responses. `FakeHttp` fails a test on any unexpected request. | `tests/fakes.py` |
| **Finder** | An adapter that finds people (and maybe emails) at a company: `csv` (your list), `pattern` (free email guesses), `apollo`, `hunter`. They run in order until someone suitable is found. | `enrich/*` |
| **Formula-injection guard** | A leading `'` added to any text cell starting with `=`, `+`, `-`, `@`, a tab or a carriage return, so a spreadsheet shows text instead of running a formula. | `outbound/csv_export.guard_cell` |
| **Freshness (`freshness_days`)** | Only jobs posted in the last N days (default 7, as of the delivery date) count. | client file; `signals.max_age_days` |
| **GitHub Actions** | GitHub's service that runs scripts on its servers. Suited to CI. For deliveries, the database would have to survive between runs, or the ledger forgets. | 3.12, 3.15 |
| **`guessed-unverified`** | Built from a name pattern (e.g. first.last@company.com). Always this label, even if a checker said "valid". | same |
| **Hand-over** | Passing leads to a sending tool (Instantly, Smartlead, a webhook). Outbound mode only; never in a client delivery. | `outbound/*` |
| **Held back** | A good lead over the client's `leads_per_week`. It is not recorded, so it goes out next time. | `delivery/run.py` |
| **Hiring Signal Report** | The product: a weekly lead file of companies hiring for the roles an agency fills. Also the default brand name (`delivery.brand_name`). | `delivery/*` |
| **Hooks (`PipelineHooks`)** | Callbacks the pipeline calls after the ICP filter, after enrichment and for each contact. The delivery layer's `LedgerHooks` uses them to drop already-delivered and do-not-list items before any paid lookup. | `pipeline.py`; `delivery/ledger.py` |
| **hot / normal / skip** | Urgency tiers from the score. `recruitment-delivery.yaml` and `demo-delivery.yaml`: `hot` >= 65 (call first), `normal` >= 15, `skip` below 15 (nothing fresh; not delivered by default). Engine defaults, if a playbook sets none (and `templates/generic.yaml`): 80 / 60. Clients receive `tiers: [hot, normal]` by default. | playbook `scoring.tiers`; `scoring.py` |
| **ICP** | "Ideal Customer Profile": the description of which companies fit. Here it means the **client's** targeting (locations, size, industries, exclusions), applied at the filter station. | playbook `icp:`; `filters.py` |
| **`_internal/`** | The folder inside each delivery that is for you only (QA, `not_delivered.csv`, the run's files). Never send it. | delivery folder |
| **Job order** | A vacancy that a company has asked an agency to fill. More job orders means more chances to place. | 5.2 |
| **KPI (key performance indicator)** | A number you watch to know whether the business is working. | 5.15 |
| **Lead** | One company plus the chosen decision-maker, with a score. | `models.py` `Lead` |
| **Ledger** | The per-client memory of every company, job and person already delivered, so nothing is delivered twice (unless `redelivery_days` allows it after N days). Held-back leads and dry runs are not recorded. It is stored in the playbook's SQLite database file (`storage.path`, default `data/leadgen.db`; demo `data/demo-delivery.db`). | `delivery/ledger.py` |
| **LLM (large language model)** | An AI model such as Claude or GPT. Used here only for optional opening lines (delivery mode), or for emails and reply sorting (outbound mode). | `llm/*` |
| **Lock file** | A file that records the exact library versions known to work. Not used yet. | 3.1 |
| **Mode** | `delivery` (default: lead files only) or `outbound` (also writes and hands over emails, handles replies). Client deliveries are always forced to `delivery`. | `modes.py` |
| **MRR (monthly recurring revenue)** | The sum of all clients' monthly plan prices. | 5.15 |
| **`not found`** | No usable email in the file: none found, it failed checking, or the client's policy (`emails.include_unverified: false`) withheld it. | same |
| **Opening line ("Suggested opening line")** | The optional 19th column: a factual first line the recruiter can adapt. It comes from a free template, or (optionally, paid) from AI under a per-run cost cap. | `delivery/opening.py` |
| **Paid lookup** | One request by an adapter marked paid: TheirStack, Apollo, Apify, `linkedin_jobs`, Hunter, MillionVerifier, ZeroBounce, NeverBounce, and the AI providers. Free adapters (Adzuna, Greenhouse / Lever / Ashby, csv / json, pattern, basic) are never paid lookups. `leadgen doctor` checks don't count either. | `registry.py` `PAID`; `usage.py` |
| **Pattern guess** | An email address built from a name and a domain (for example first.last@company.com) by the free `pattern` finder. Always labelled `guessed-unverified`. | `enrich/pattern.py` |
| **Pilot** | A short trial of the report, for example 4 weekly deliveries, before a monthly plan. | 5.6 |
| **Placement** | A candidate the agency introduced being hired: the moment a contingency agency gets paid. | 5.2 |
| **Playbook** | A YAML settings file describing *how* leads are made: sources, how people are found, which email checker, scoring, database file, mode. Shared by many clients. | `playbooks/*.yaml`; defaults in `playbook.py` `DEFAULTS` |
| **Postgres** | A database server that many users and processes can write to at once. A future option if the single SQLite file becomes a limit. | 3.5 |
| **Pre-score** | A provisional score without a contact, used to rank companies so enrichment money goes to the best ones first. | `pipeline.py` stage 4 |
| **Primary signal** | The signal type(s) that drive the score and must be present (`signals.primary`). Client deliveries always include `job_posting`. | playbook `signals:`; `delivery/run.py` |
| **QA summary** | The check printed after each delivery: volumes vs target, reasons for leaving things out, duplicates, do-not-list hits, email-label counts, warnings, API usage and estimated cost. | `delivery/qa.py` |
| **Re-post** | An old job ad put up again to look new. Detected by comparing with jobs seen in earlier runs (so it needs history), and left out of client deliveries by default (`drop_reposts: true`). | `store.observe_signals`; `signals.py` |
| **Rehearsal** | A real (not dry) run on a **copy** of the database, into a scratch folder, so the real ledger is untouched. Never send its files. | 4.5.5, 6.6 |
| **Retained agency** | An agency paid part of its fee upfront; usually a senior-level search firm. Not the first target. | 5.2 |
| **`risky`** | A real address from a provider or list that is not confirmed (catch-all mail server, or never checked). Usually works; expect some bounces. | same |
| **Run ID** | The id of one pipeline run, such as `20260925-064525-e79dae` (UTC time plus 6 random hex characters). It names the run's folder. | `store.py` `start_run` (called by `pipeline.py`) |
| **SaaS (software as a service)** | Software that customers log in to and use. "Stage 3" in section 3; not built. | 3.0 |
| **Score** | 0-100, built from four parts: intent, fit, reachability and extra signals. | `scoring.py`; 2.8 |
| **Service account** | A robot Google account that owns a key. Needed for the Google Sheets push. | 3.10 |
| **Signal** | A dated event that suggests a company needs something now. Here the main signal is a **job posting** (shown as `Hiring`). Other types (funding, expansion, leadership change ...) only add bonus points in the delivery playbooks. | `models.py` `SignalType`; `signals.py`; playbook `signals:` |
| **SLA (service-level agreement)** | What you promise about the service. | 5.12 |
| **SOP (standard operating procedure)** | A written step-by-step routine, for example for a virtual assistant. | 5.17 |
| **Source** | An adapter that fetches companies and their signals: `adzuna`, `greenhouse`, `lever`, `ashby`, `csv`, `json`, `theirstack`, `apollo`, `apify`, `linkedin_jobs` (use at own risk). | `sources/*` |
| **SQLite** | A database stored as a single file, with no server. leadgen keeps all its memory in one. | `store.py`; 2.10 |
| **Stage (lead stage)** | A lead's position in the funnel: `sourced` -> `qualified` -> `enriched` -> `verified` -> `ready` -> `exported` -> `replied` -> `positive` -> `booked` -> `won`, plus `lost`. | `models.py` `Stage` |
| **Stage 0-3 (growth stage)** | Planning buckets used in section 3: now (1-4 clients), 5-10 clients, 20-50 clients or a first hire, selling software. | 3.0 |
| **Suppression / do-not-list** | People and companies that must never appear. **Global** (per database file, all clients) or **per client** (`--client NAME`, plus the client file's `exclusions`). Kinds: email, domain, company, linkedin. Companies are removed before any paid lookup; people are skipped as soon as they are found. | `leadgen suppress ...`; `store.py`; `delivery/ledger.py` |
| **Token** | The unit AI providers count and charge for (roughly three-quarters of a word). | `usage.py` |
| **Unit economics** | What one client brings in, minus what it costs to serve them. | 5.7 |
| **Usage lines** | The `API usage: ...` lines printed after each run and delivery: paid lookups against the cap, calls per adapter, credits left, estimated cost. | `usage.py` `summary_lines()`; 4.9.3 |
| **VA (virtual assistant)** | A part-time assistant who could run deliveries, spot-checks and prospect research. | 5.17 |
| **venv (virtual environment)** | A private copy of Python just for this project (`python3 -m venv .venv`). | 4.2 |
| **`verified`** | The mailbox was confirmed deliverable by a checking service or by the list / provider that supplied it, **and** the address was not built from a pattern. | `delivery/rows.py` |
| **Verifier** | An adapter that checks an email: `basic` (free, offline; catches typos and throw-away domains, **cannot** confirm a mailbox), `hunter`, `millionverifier`, `zerobounce`, `neverbounce` (paid). | `verify/*` |
| **VPS (virtual private server)** | A rented, always-on computer. A future home for scheduled runs. | 3.13 |
| **WAL (write-ahead log)** | An SQLite mode that keeps extra files next to the database. leadgen does not use it, so copying the `.db` file while no leadgen command runs gives a complete backup. | 4.11 |
| **Waterfall** | Contact finders run in order until a person with a wanted title and a usable email (or email guesses) is found, so paid finders are not called when a free one already succeeded. | `contacts.ContactWaterfall` |
| **Webhook** | A URL that another service calls to pass data (Slack alerts, the outbound webhook exporter, the reply server). | `notify/*`, `outbound/webhook.py`, `server.py` |
| **YAML** | The plain-text settings format of playbooks and client files: `key: value` lines, indented with spaces (never tabs), lists written `[a, b, c]`. | `playbooks/`, `clients/` |

## Appendix D. Other documents in the repository

This document summarises and cross-checks the documents below. They remain the sources of truth for their own areas; where this document found them out of date, the drift is listed in [6.7](#67-known-limitations).

| Document | What it is | Main parts |
|---|---|---|
| [README.md](README.md) | The owner guide (1,136 lines): the main user-facing manual. Tests check its command table and internal links. | [What the client gets](README.md#what-the-client-gets); [Quick start: delivery mode, offline, no API keys](README.md#quick-start-delivery-mode-offline-no-api-keys); [Your first real client](README.md#your-first-real-client); [The client file](README.md#the-client-file); [Freshness](README.md#freshness) (one stale sentence, see 6.7); [The ledger](README.md#the-ledger-never-the-same-thing-twice); [Email statuses](README.md#email-statuses); [The QA summary](README.md#the-qa-summary); [Cost control](README.md#cost-control); [Live readiness](README.md#live-readiness-leadgen-doctor); [Sources, compliance and do-not-lists](README.md#sources-compliance-and-do-not-lists); [Weekly automation](README.md#weekly-automation); [Outbound mode (optional)](README.md#outbound-mode-optional); [Commands](README.md#commands); [Extending: your own adapter](README.md#extending-your-own-adapter); [Troubleshooting](README.md#troubleshooting); [Project layout](README.md#project-layout) |
| [PLAN.md](PLAN.md) | The business plan (134 lines). | 1 The business; 2 Pricing ideas (rough suggestions, not market research); 3 The weekly workflow; 4 What's built; 5 Switched off, kept for later; 6 Risks and still to do; 7 Setup checklist; 8 Numbers to watch |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Module contracts for developers (338 lines); consistent with section 2 of this document. | Modes; Module layout; The run (`Pipeline(...).run()`); The delivery layer; Usage metering and the paid-lookup budget; Live readiness; Risk flags; Store; Adapter contracts; Configuration and environment |
| [docs/DELIVERY.md](docs/DELIVERY.md) | The **binding** contract of `leadgen/delivery/` (194 lines). Change it together with the code and the tests that pin it. | Client file: `clients/<name>.yaml`; Modules; CLI (added) |

Two files also work as in-place documentation: [clients/_template.yaml](clients/_template.yaml) explains every client-file key, and [playbooks/templates/generic.yaml](playbooks/templates/generic.yaml) explains every playbook setting (its comment on the default AI model is out of date, see 6.7).
