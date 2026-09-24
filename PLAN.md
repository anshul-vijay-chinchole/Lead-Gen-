# Lead-Gen Engine - Plan

A **niche-agnostic, signal-led** lead-generation engine. One engine, many niches:
each niche (or client) is a **playbook** - a settings file - not new code.

It works anywhere. No country or legal rules are built in: you choose where you
sell and which rules apply to you (the playbook has a footer line for an opt-out
sentence, and a do-not-contact list).

---

## 1. The idea: a factory line

Companies go in one end; people worth talking to come out the other. Every
station below is **built** and runs today (offline demo:
`leadgen run -p playbooks/demo-offline.yaml`, no keys needed).

| # | Station | What it does | Tools it can use |
|---|---|---|---|
| 1 | **Find** | Companies with a reason to buy *now*: hiring, funding, growth, reviews, ... | Your own CSV/JSON files, TheirStack, Adzuna, Apollo, Apify (e.g. Google Maps), Greenhouse / Lever / Ashby job boards |
| 2 | **Filter** | Drop the wrong place, size or industry, competitors, existing clients, the do-not-contact list | - |
| 3 | **Find the person** | The decision-maker, from your list of job titles (best first) | Your own CSV, Apollo, Hunter, free email-pattern guessing |
| 4 | **Check the email** | Verify before sending so emails don't bounce; guessed addresses must come back "valid" | MillionVerifier, ZeroBounce, NeverBounce, Hunter, free syntax check |
| 5 | **Score** | 0-100: how strong the reason is, how well they fit, can we reach them -> hot / normal / skip | - |
| 6 | **Write** | A short email + 3 follow-ups about *why them, why now*, with copy checks | Claude or OpenAI (or any compatible API); free templates as fallback |
| 7 | **Hand over** | Ready leads go to your sending tool (the engine never sends email itself); every lead also lands in a review sheet | Instantly / Smartlead (upload CSV or API), webhook (Zapier / Make / n8n); review sheet as CSV or Google Sheets |
| 8 | **Read replies** | Sort replies: interested, question, referral, not now, out-of-office, no, unsubscribe, bounce - and act on each | CSV import or a webhook server; rules or AI |
| 9 | **Alert** | Interested replies arrive with a draft answer and your booking link | Slack, webhook, terminal |
| 10 | **Report** | Funnel numbers, plus a one-page "live opportunities" report to win clients | `leadgen stats`, `leadgen demo` |

**Playbooks shipped:** `demo-offline.yaml` (try it), `my-agency.yaml` (finds
clients for *your* agency) and templates in `playbooks/templates/`
(generic, recruitment, saas-funding, local-business, agency-outreach).
Start a new one with `leadgen init my-niche --template generic`.

## 2. Safety rules already built in

- Nobody is handed over twice by the same playbook, and the same address is not
  handed over again from another playbook within `dedupe_days` (90 by default).
- No colleague at a company contacted in the last 30 days
  (`company_cooldown_days`). Once anyone at a company replies, says no,
  unsubscribes or bounces, that playbook never contacts the company again
  (out-of-office auto-replies don't count).
- "No", unsubscribe and bounce replies go on the do-not-contact list
  automatically; add your own with `leadgen suppress add`.
- Guessed addresses (first.last@...) need a real "valid" from a verifier.
- No AI money is spent on leads that could not be handed over anyway, and the
  AI switches itself off after repeated failures (templates take over).
- `--dry-run` is a free rehearsal: no paid calls, upload files are written as
  `*.dry-run.csv`, and nothing is recorded as sent.
- API keys and tokens are masked in logs and error messages.

## 3. Still to do

| What | Why / notes |
|---|---|
| **Live check with real API keys** | Every paid connector (TheirStack, Adzuna, Apollo, Apify, Hunter, the verifiers, Instantly, Smartlead, Google Sheets, Slack, Claude / OpenAI) was built from the provider's documentation and tested against sample responses - never against the live service. When you get each key: `leadgen validate`, then a small `leadgen run --limit 10`, and fix whatever the real service does differently. |
| **LinkedIn channel** | Not built. Email only for now. |
| **Dashboard** | Not built. Today: the command line, CSV files and Google Sheets. |
| **Scheduling** | Runs start when you type the command. For a daily run, use cron (Mac/Linux) or Task Scheduler (Windows). |
| Later, only if clients need it | Direct CRM sync (beyond the webhook), our own email sender, phone / voice, a multi-user web app. |

## 4. Your setup checklist

Do these in parallel - new inboxes need 2-3 weeks of warm-up before real sending.

1. Buy 2-3 look-alike domains. Never send cold email from your main domain.
2. Create 2-3 inboxes per domain and set up SPF, DKIM and DMARC.
3. Connect them to Instantly or Smartlead and switch on warm-up.
4. Get API keys as you need them (most tools have a free tier or trial - check
   current pricing). For AI writing you need an **API account** from Anthropic
   or OpenAI; a Claude.ai or ChatGPT chat subscription does not include API access.
5. Copy `.env.example` to `.env` and fill in only what your playbook uses.
6. `leadgen validate -p playbooks/my-agency.yaml` -> `leadgen run ... --dry-run`
   -> `leadgen run ... --limit 10` -> review `opportunities.csv` before importing anything.
7. Check the cold-email rules for the places you sell into - that is your call,
   not the engine's.

## 5. Everyday commands

```
leadgen run       -p playbooks/X.yaml [--dry-run] [--limit N]   # find -> ... -> write -> hand over
leadgen leads     -p playbooks/X.yaml                           # the leads of the last run
leadgen demo      -p playbooks/X.yaml --prospect "Acme"         # one-page sales report
leadgen replies   -p playbooks/X.yaml --file replies.csv        # sort + act on replies
leadgen serve     -p playbooks/X.yaml --token <secret>          # receive replies by webhook
leadgen stats     -p playbooks/X.yaml                           # funnel numbers
leadgen followups -p playbooks/X.yaml                           # who to get back to today
leadgen mark      --email jane@acme.com --stage booked          # record a booked call / a win
```

## 6. Numbers to watch

Rough targets for well-targeted outbound (they vary a lot by niche):
bounce rate under 2%, reply rate 3-8%, positive replies 1-3% of emails sent,
more than 70% of booked calls actually happen. `leadgen stats` shows the funnel
and replies by category.

## 7. What you sell

The engine fits any niche; your **pitch** should be one niche at a time ("I get
<niche> companies conversations with <buyers> who are showing <signal> right
now"). Start with a short paid pilot, then a monthly retainer once there are
results. Promise conversations, not revenue, and agree in writing what counts
as "qualified".
