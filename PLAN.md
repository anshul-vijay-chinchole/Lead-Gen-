# Plan: the Hiring Signal Report

## 1. The business

**What we sell.** A weekly **Hiring Signal Report** for recruitment and staffing
agencies. Each client gets a file of companies that posted a job **in the last few days**
for the roles that agency fills. Each row has the job link and date, the person most
likely to own the hire, their email with an **honest label**, and a 0-100 score (hot
leads first). A client never receives the same company, job or person twice.

**What we don't do.** We don't email, call or message anyone, for ourselves or for our
clients. The agency does its own outreach. The engine enforces this: `mode: delivery`
(the default) stops after scoring and writing files.

**Why an agency pays for it.**
- **Timing.** A company that posted a job yesterday has a live need, and the first
  agency to call has the edge.
- **Time saved.** It replaces hours of job-board trawling and LinkedIn research per week.
- **Nothing twice.** The ledger removes everything already delivered, so every file is new.
- **No surprises.** Every email says whether it is `verified`, `risky`,
  `guessed-unverified` or `not found`. A guess is never sold as verified.

## 2. Pricing ideas (rough suggestions, not market research)

These are starting points to test with the first few clients and adjust. They are not
researched market prices.

| Offer | What's in it | Rough idea |
|---|---|---|
| **Pilot** | 2-4 weekly reports, 10-15 leads a week, one niche, one region | Free or a small fee: it proves the quality and gives you a testimonial. |
| **Starter** | ~25 leads a week, one niche, one region, CSV + Excel + HTML | A flat monthly fee. |
| **Growth** | ~50 leads a week, several niches or regions, paid email checks (more `verified`), opening lines, a shared Google Sheet | A higher monthly fee that covers the paid tools. |
| **Exclusive** add-on | You sell a niche + region to only one agency | A premium on top of either plan. |

How to set the numbers:
- **Know your cost per delivery.** Every delivery prints its API usage and, once
  `usage.cost_per_call` holds your plans' prices, an estimated cost. The free default
  costs nothing but your time. Paid tools (Apollo, Hunter, a verifier, AI) add a cost per
  lead. Price at a healthy multiple of cost plus your time.
- **Charge per week or month, not per lead.** Volume depends on the job market, and a
  quiet week shouldn't break the deal. Agree a *target* (`leads_per_week`), not a
  guarantee.
- **Don't promise "verified emails"** unless you pay for email checks. Even then, promise
  only the labels, not a bounce rate.

## 3. The weekly workflow

```bash
leadgen clients                                  # who is due, what they received so far
leadgen doctor --client acme                     # keys still working? (free checks; monthly or after changes)
leadgen deliver --client acme                    # the delivery: files + QA summary
```

1. Run `leadgen deliver` for each client (by hand, or with cron: see README "Weekly automation").
2. **Read the QA summary**: volume vs target, verified email rate, warnings (low volume,
   budget reached, a source failed).
3. **Spot-check 3-5 rows**: open the job links, check the decision-maker is still there.
4. **Send the files** in the delivery folder, never `_internal/`.
5. **Act on feedback**: change the client file (`roles`, `locations`, `exclusions`,
   `leads_per_week`). Put anyone who asks not to be listed on the do-not-list
   (`leadgen suppress add ...`, or `--client NAME` for one client).

Monthly: check volume trends (`leadgen clients`), costs (the usage lines), and whether a
client's niche is running dry (many "already delivered to this client" in the QA summary).

## 4. What's built

| Area | What works today |
|---|---|
| **Client files** | `clients/<name>.yaml`: roles, locations, size, industries, exclusions, volume, freshness, dedupe, tiers, email policy, opening lines, budget, formats, folder, Google Sheet, branding, overrides. Typos are caught with a "did you mean". `leadgen clients new NAME` creates one. |
| **`leadgen deliver`** | One command per client: CSV (Excel-friendly), Excel (Leads + About sheets) and a branded HTML one-pager. Optional Google Sheets push. `--dry-run` writes PREVIEW files and records nothing. |
| **Ledger** | Per client: every delivered company, job and person. Nothing is delivered twice (`redelivery_days` can allow it again after N days). Leads over the weekly limit are held back for next time. |
| **Honest emails** | Four labels with fixed meanings. Pattern guesses are always `guessed-unverified`. `include_unverified: false` delivers verified emails only. |
| **Freshness** | Only jobs posted in the last `freshness_days`. Undated jobs and re-posted ads are left out by default. |
| **QA summary** | Found / qualified / delivered vs target, top reasons for leaving things out, duplicates, do-not-list hits, held back, verified rate, email status counts, warnings, API usage + cost. Saved with `not_delivered.csv` in `_internal/`. |
| **Cost control** | Free by default (Adzuna, job boards, CSV, pattern guesses, basic check). `--budget N` / `budget.max_paid_lookups` cap paid lookups before they reach the network. Usage and estimated cost are printed after every run. |
| **Opening lines** | Optional column: free template lines, or AI lines with a per-run cost cap and fact checks. |
| **`leadgen doctor`** | One free live check per API key, with quota where the provider reports it. |
| **Do-not-lists** | Global (emails, domains, company names, LinkedIn profiles) and per client. |
| **Sources / finders / checkers** | Adzuna, Greenhouse / Lever / Ashby, CSV / JSON, TheirStack, Apollo, Apify; Apollo, Hunter, CSV, pattern finders; basic, MillionVerifier, ZeroBounce, NeverBounce, Hunter checkers. |
| **Demo** | `leadgen deliver --client demo-client`: fully offline on made-up data. `leadgen demo` makes a one-page sample report for a prospect. |
| **Tests** | About 2,000 automated tests. No test touches the network: every provider is tested against canned responses in its documented format. |

## 5. Switched off, kept for later

- **Outbound mode** (`mode: outbound`): AI / template email sequences, hand-over to
  Instantly / Smartlead, reply sorting (`leadgen replies` / `serve`), follow-ups. It
  still works and is tested. `playbooks/my-agency.yaml` uses it to find clients for your
  own business. Rewrite its `offer` section to pitch the Hiring Signal Report first.
  Delivery mode never touches any of it.
- **Paid tools** (TheirStack, Apollo, Hunter, paid email checkers, AI) are in the
  playbooks but `enabled: false`, until a client's price covers them.
- **LinkedIn / Indeed scraping** (`linkedin_jobs`, the Apify presets) is marked "use
  at own risk" and is not in any default playbook.

## 6. Risks and still to do

| What | Why / what to do |
|---|---|
| **Live checks with real keys** | Every connector (Adzuna, TheirStack, Apollo, Hunter, the email checkers, Google Sheets, AI, Slack) was built from the provider's documentation and tested against sample responses, never against the live service. As you get each key: `leadgen doctor`, then a small real delivery (`--budget 20`), and fix whatever the real service does differently. |
| **Data-provider terms** | Reselling data is often restricted. Before selling a report built on a provider, read its terms: does it allow sharing its data (job ads, company data, contact details) with your clients? Job ads usually belong to the employer or the job board. Prefer company career pages (Greenhouse / Lever / Ashby), official APIs whose terms fit, and your own research. |
| **Personal data / privacy law** | The report names real people and their work emails. Many places regulate this (for example UK / EU data-protection law). Check the rules for you, your clients and the people listed: lawful basis, telling people, opt-outs, how long you keep data. Honour every "don't list me" with `leadgen suppress`. This is not legal advice. |
| **Data accuracy** | A job may already be filled, a decision-maker may have left, company size may be unknown or out of date. Spot-check every week. Show the date posted and the source so clients can judge. |
| **Client expectations on emails** | Explain the four email labels before the first delivery (the Excel "About" sheet has the legend). On the free setup most emails are `guessed-unverified` or `not found`, and no names at all come from Adzuna alone. Sell it as a hiring-signal list with the decision-maker where we can find one, or budget for Apollo / Hunter / a checker. |
| **Volume** | One free source (Adzuna) may be thin for narrow niches or small regions. Add company job-board watchlists, your own CSV lists, or TheirStack. The QA summary warns when volume is below target. |
| **Sources don't follow the client's roles** | The source search words (Adzuna `queries`) are set in the playbook, not taken from the client's `roles`. A client in a different niche needs its own copy of the playbook. Worth automating later. |
| **Ledger safety** | `data/*.db` holds what each client received. Back it up: losing it means re-delivering old leads. |
| **Client files in git** | `deliveries/` (client data), `output/`, `.env` (keys) and the databases (`*.db`) are in `.gitignore`, so they are never committed. Keep them private: share a delivery only with its client, and back up `data/*.db` (see Ledger safety). |
| **Not built** | Emailing the report automatically (on purpose: review first), a client portal / dashboard, billing, CRM sync. |

## 7. Setup checklist

1. Install and run the demo: `leadgen deliver --client demo-client`, then open the files.
2. Get a free Adzuna key and fill in `.env` (`cp .env.example .env`): `ADZUNA_APP_ID`,
   `ADZUNA_APP_KEY`, `SENDER_NAME`, `SENDER_EMAIL`, `SENDER_WEBSITE`.
3. Set Adzuna's `countries:` and `queries:` in `playbooks/recruitment-delivery.yaml` to
   your market and niche.
4. Make a sample report for prospects: a real delivery for a made-up client in their
   niche (`leadgen clients new sample-finance`), or `leadgen demo`.
5. First client: `leadgen clients new NAME` -> edit -> `leadgen doctor --client NAME` ->
   `leadgen deliver --client NAME` -> spot-check -> send.
6. When a client's price allows it, switch on Hunter / Apollo (names + emails) and a
   paid checker (verified emails), with a `budget` in the client file.
7. Check the data-provider terms and the privacy rules above before selling.

## 8. Numbers to watch

- **Leads delivered vs target** per client (QA summary; `leadgen clients`).
- **Verified email rate** and the share of `not found`.
- **"Already delivered" share**: when most matches were already sent, widen the
  client's roles or locations, or add sources.
- **Cost per delivery** (usage lines) vs what the client pays.
- **Client outcomes**: ask each month how many leads turned into calls, jobs or
  placements. That is what renews the subscription.
