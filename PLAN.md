# Lead-Gen Engine — Build Plan (v1)

A **niche-agnostic** lead-generation pipeline for US / UK / Tier-1 markets.
One engine, many niches: each niche is a **playbook** (a settings file), not new code.

---

## 1. The idea in plain English

Think of it as a **factory line**. Raw material goes in one end (companies on the internet),
each station does one job, and out the other end come **people who want to talk**.

| # | Station | What it does, in plain words |
|---|---|---|
| 1 | **Find** | Pull in companies that have a *reason to buy right now* (hiring, raised money, expanding, new boss, etc.). |
| 2 | **Filter** | Throw away anything that isn't the client's ideal customer (wrong country, size, industry, competitors, existing clients). |
| 3 | **Find the person** | Work out *who* at that company makes the decision (e.g. VP Finance, Founder, Head of Ops). |
| 4 | **Get + check email** | Find their work email and verify it, so emails don't bounce and hurt our sending reputation. |
| 5 | **Score** | Give every lead a 0–100 score: how hot is the reason, how well do they fit, can we reach them. |
| 6 | **Write** | AI writes a short, specific email + 3 follow-ups that mention *why this company, why now*. |
| 7 | **Send** | Load the leads into a sending tool (Instantly / Smartlead) that sends slowly from warmed-up inboxes. |
| 8 | **Read replies** | AI sorts replies: interested / wrong person / not now / not interested / out-of-office / unsubscribe. |
| 9 | **Hand off** | Interested replies get pushed to Slack/email with a booking link. A human takes the call. |
| 10 | **Report** | Weekly numbers: found → verified → sent → replied → interested → booked. |

**The playbook** is the "recipe card" for a niche: which signals to look for, who the ideal
customer is, which job titles to contact, and what the angle of the email is.
New niche = new recipe card. No code changes.

---

## 2. What gets built now (v1)

### Built in code (this is the part we own — the "brain")
- **Playbook system** — YAML file per niche/client.
- **Source adapters** — pluggable "inputs":
  - CSV import (works with *any* export: Apollo, Clay, Sales Navigator, Apify, spreadsheets)
  - Apollo API (company + people search)
  - Job-postings source (hiring signals)
- **Filter** — country allow-list, size, industry, keywords, competitor + suppression lists.
- **Contact + email waterfall** — try provider A, then B (Apollo → Hunter), keep the best-titled decision-maker.
- **Email verification** — MillionVerifier / ZeroBounce: `valid`, `risky (catch-all)`, `invalid`.
- **Scoring engine** — 0–100, weights set in the playbook; tiers `hot` / `normal` / `skip`.
- **AI writer** — OpenAI or Claude API, with guardrails (length, no clichés, must reference the signal),
  plus a no-API template fallback so the engine runs at $0.
- **Exports** — Google-Sheet-ready CSV + Instantly/Smartlead upload CSV.
- **Reply classifier** — sorts replies, extracts referrals / return dates, alerts Slack on interested ones.
- **Memory (SQLite)** — never email the same person twice, suppression list, run history, funnel stats.
- **Demo report generator** — "5 live opportunities for *your* business" one-pager: the sales asset used to win clients.
- **Two starter playbooks**
  1. `my-agency.yaml` — finds clients **for you** (outreach to agencies / B2B service firms).
  2. `recruitment-clients.yaml` — example client playbook (companies actively hiring → hiring managers).

### Rented tools (commodities — never build these)
| Job | Tool (pick one) | Approx. cost* |
|---|---|---|
| Company/people data | Apollo (free tier to start) | $0 – $99/mo |
| Email finder fallback | Hunter / Prospeo | $0 – $49/mo |
| Email verification | MillionVerifier | ~$37 per 10k |
| AI writing | OpenAI API or Claude API (**not** a ChatGPT subscription — that has no API) | ~$2–10 per 1k leads |
| Sending + warm-up | Instantly or Smartlead | ~$37/mo |
| Sending inboxes | 3 spare domains + 6–9 Google Workspace / Outlook inboxes | ~$40–70/mo |
| Booking | Cal.com / Calendly | $0 |
| Alerts | Slack webhook | $0 |

*Check current pricing — ballpark only. Realistic starting spend: **~$100–200/month**.

### Deliberately NOT built now
Dashboard, CRM, our own email sender, LinkedIn automation, AI voice calling, phone dialers, SaaS login.
All of these come **after** paying clients prove what's needed.

---

## 3. How the pieces connect

```
 playbook.yaml ─────────────────────────────────────────────────────────┐
                                                                         ▼
 [CSV / Apollo / Jobs] → Filter → Contact waterfall → Verify → Score → AI write
                                                                         │
                          ┌──────────────────────────────────────────────┘
                          ▼
        opportunities.csv (Google Sheet)   +   outbound.csv (Instantly/Smartlead)
                                                        │  sends day 1 / 3 / 7 / 12
                                                        ▼
                                       replies.csv / webhook → AI classify
                                                        │
                                  interested → Slack alert + booking link → call
                                                        │
                                              SQLite memory + funnel stats
```

Commands (v1):
```
leadgen run      --playbook playbooks/X.yaml     # find → … → write → export
leadgen demo     --playbook playbooks/X.yaml     # 1-page "live opportunities" sales report
leadgen replies  --playbook playbooks/X.yaml --file replies.csv
leadgen stats    --playbook playbooks/X.yaml     # funnel numbers
```

---

## 4. Compliance (US / UK / Tier-1) — built into the engine

| Country | Rule of thumb for cold B2B email | Engine behaviour |
|---|---|---|
| US | CAN-SPAM: allowed, needs opt-out + real postal address + honest subject | Adds opt-out + address footer |
| UK | PECR/GDPR: OK to **corporate** addresses with opt-out + legitimate interest; not sole traders | Flags/filters sole traders & personal domains (gmail etc.) |
| EU (DE, FR, NL…) | Stricter; Germany especially | Off by default |
| Canada | CASL: needs (implied) consent — risky | Off by default |
| Australia | Spam Act: needs (inferred) consent | Off by default |

Start with **US + UK**. Other countries are a playbook switch once you've decided the risk is acceptable.
(Not legal advice.)

---

## 5. Timeline

**Do these today, in parallel with the build — inbox warm-up takes 2–3 weeks:**
1. Buy 3 look-alike domains (e.g. `getyourbrand.com`, `tryyourbrand.com`). Never send cold from your main domain.
2. 2–3 inboxes per domain (Google Workspace or Outlook), set SPF/DKIM/DMARC.
3. Connect them to Instantly/Smartlead and switch on warm-up.
4. Create accounts/keys: Apollo (free), OpenAI or Anthropic API, MillionVerifier, Slack webhook.

| Milestone | Build | Result for you |
|---|---|---|
| **M1** | Core engine, playbooks, CSV input, filter, scoring, AI writer, exports, memory | Run it on an Apollo CSV export → get ready-to-send campaigns |
| **M2** | Apollo API, jobs source, email waterfall, verification | Fully automatic lists, no manual exports |
| **M3** | Reply classifier, Slack alerts, stats, demo report | Close the loop + sales asset for winning clients |

**Week 3–4 (you, selling):** run `my-agency.yaml`, build demo reports for the best 20 prospects,
send 20–30 personal outreaches/day. Target: first paid pilot.

---

## 6. Success numbers to track

Rough targets for well-targeted outbound (varies a lot by niche):
- Bounce rate **< 2%** (verification working)
- Reply rate **3–8%**
- Positive replies **1–3%** of sent
- Show-up rate on booked calls **> 70%**

Every lead carries its stage so these come straight out of `leadgen stats`.

---

## 7. What you sell (engine is generic, pitch stays specific)

Engine = any niche. **Pitch** = one niche at a time ("I get <niche> companies conversations with
<buyers> who are showing <signal> right now"). Starting offer: **30-day pilot, $500–750**, then
$1,000–1,500/mo once you have results. Guarantee conversations, not revenue, and define
"qualified" in writing.
