"""Tests for leadgen.replies: cleaning, rule/AI classification, actions, CSV + webhook inputs."""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from leadgen.llm.base import LLMError
from leadgen.models import Company, Contact, Lead, Reply, ReplyCategory as RC, Stage
from leadgen.replies import (
    AI_SYSTEM_PROMPT, classify, classify_ai, classify_rules, clean_reply_text, extract_bounced_emails,
    handle_reply, load_replies_csv, name_from_email, ooo_return_date, parse_webhook_payload,
    suggest_reply, timing_follow_up,
)
from tests.conftest import TODAY
from tests.fakes import FakeLLM

HOOK_URL = "https://hooks.example.com/leadgen/abc"
OFFER = {"sender_name": "Tom Sender", "sender_company": "Northwind Talent", "service": "finance recruitment",
         "value_prop": "we fill finance roles in under 3 weeks", "booking_link": "https://cal.com/tom/intro",
         "sender_website": "https://northwind.io"}


def R(body: str = "", frm: str = "jane@acme.com", subject: str = "Re: Quick question", **kw) -> Reply:
    return Reply(from_email=frm, body=body, subject=subject, **kw)


def seed_lead(ctx, email="jane@acme.com", name="Jane Doe", company="Acme", domain="acme.com",
              title="CFO", exported=True) -> Lead:
    lead = Lead(company=Company(name=company, domain=domain),
                contact=Contact(full_name=name, email=email, title=title),
                playbook=ctx.playbook.name, stage=Stage.READY, score=82, tier="hot")
    ctx.store.save_lead(lead)
    if exported:
        ctx.store.mark_exported(lead.id, "csv")
    return lead


def stage_of(ctx, lead) -> str:
    return ctx.store.get_lead(lead.id).stage


# =============================================================================
# clean_reply_text
# =============================================================================

@pytest.mark.parametrize("raw,expected", [
    ("Sounds great, Tuesday works.\n\nOn Thu, Sep 24, 2026 at 9:00 AM Tom <tom@northwind.io> wrote:\n"
     "> Hi Jane,\n> Worth a quick chat?\n", "Sounds great, Tuesday works."),
    # Gmail wraps long attribution lines
    ("Yes please.\n\nOn Thu, Sep 24, 2026 at 9:00 AM Tom Sender <\ntom@northwind.io> wrote:\n\n> Hi",
     "Yes please."),
    ("Not interested.\n\n________________________________\nFrom: Tom <tom@northwind.io>\n"
     "Sent: Thursday, September 24, 2026 9:00 AM\nTo: Jane <jane@acme.com>\nSubject: Hi\n\nHi Jane",
     "Not interested."),
    ("Call me.\r\n-----Original Message-----\r\nFrom: Tom\r\nHi", "Call me."),
    ("Speak to Bob.\nFrom: Tom Sender\nDate: 24 Sep 2026\nSubject: hi\n\nbody", "Speak to Bob."),
    ("Happy to chat.\n-- \nJane Doe\nCFO, Acme\njane@acme.com", "Happy to chat."),
    ("Yes!\n\nSent from my iPhone", "Yes!"),
    ("Sounds good, send me times.\n\nBest,\nJane Doe\nCFO | Acme Inc\n+1 555 0100\njane@acme.com",
     "Sounds good, send me times."),
    ("Sounds good.\nThanks, Jane", "Sounds good."),
    ("Hi,\nThanks\nNot interested", "Hi, Thanks Not interested"),  # not a signature block
    ("Thanks!", "Thanks!"),
    ("> only quoted\n> text", ""),
    ("R&amp;D team &nbsp; says   hi", "R&D team says hi"),
    ("Line one\n\n\n   line   two\t\tend", "Line one line two end"),
    (None, ""),
    ("  \n\t ", ""),
])
def test_clean_reply_text_plain(raw, expected):
    assert clean_reply_text(raw) == expected


def test_clean_reply_text_html():
    html_body = (
        "<html><head><style>p{color:red}</style><title>x</title></head><body>"
        "<div dir='ltr'>Sounds good &amp; let&#39;s talk<br>Tuesday?</div>"
        "<div class=\"gmail_quote\"><div class=\"gmail_attr\">On Thu, Tom wrote:</div>"
        "<blockquote class=\"gmail_quote\">Hi Jane, worth a chat?</blockquote></div></body></html>")
    assert clean_reply_text(html_body) == "Sounds good & let's talk Tuesday?"


def test_clean_reply_text_html_nested_blockquotes_and_outlook():
    assert clean_reply_text("<p>Interested!</p><blockquote type='cite'><div>Old <blockquote>older"
                            "</blockquote> stuff</div></blockquote><p>tail</p>") == "Interested! tail"
    outlook = ("<div>Not now, thanks.</div><hr><div id=\"divRplyFwdMsg\"><b>From:</b> Tom<br>"
               "<b>Sent:</b> Monday</div><div>Hi Jane</div>")
    assert clean_reply_text(outlook) == "Not now, thanks."
    assert clean_reply_text("<ul><li>one</li><li>two</li></ul><!-- hidden -->") == "- one - two"


def test_clean_reply_text_keeps_angle_brackets_in_plain_text():
    assert clean_reply_text("Talk to Jane <jane@acme.com> instead") == "Talk to Jane <jane@acme.com> instead"


# =============================================================================
# classify_rules - categories and precedence
# =============================================================================

@pytest.mark.parametrize("body,category", [
    # positive
    ("Sounds good, let's chat next week", RC.POSITIVE),
    ("Yes, send over some info", RC.POSITIVE),
    ("I'm interested - what times work for you?", RC.POSITIVE),
    ("Happy to chat. I'm free on Tuesday afternoon.", RC.POSITIVE),
    ("Please don't hesitate to send me times", RC.POSITIVE),
    ("No worries, happy to chat", RC.POSITIVE),
    ("Not sure yet, but tell me more", RC.POSITIVE),
    ("Sure", RC.POSITIVE),
    # question
    ("How much does it cost?", RC.QUESTION),
    ("Interesting - how does it work?", RC.QUESTION),
    ("What's your pricing", RC.QUESTION),
    ("I'm not sure. Who else do you work with?", RC.QUESTION),
    # negative
    ("Not interested, thanks", RC.NEGATIVE),
    ("No thanks", RC.NEGATIVE),
    ("No, thank you.", RC.NEGATIVE),
    ("We're all set", RC.NEGATIVE),
    ("We use an in-house team for this", RC.NEGATIVE),
    ("I wouldn't be interested", RC.NEGATIVE),
    ("This isn't something we'd be interested in", RC.NEGATIVE),
    ("Not a fit for us right now", RC.NEGATIVE),
    ("Please don't. We already have an agency.", RC.NEGATIVE),
    ("No.", RC.NEGATIVE),
    # timing
    ("no, not right now", RC.TIMING),
    ("Not at the moment. Try again in 2 weeks.", RC.TIMING),
    ("No thanks, maybe try us next quarter", RC.TIMING),
    ("I'm not interested right now, check back next quarter", RC.TIMING),
    ("Reach out in 3 months", RC.TIMING),
    ("Check back after Q1", RC.TIMING),
    ("Maybe in the new year", RC.TIMING),
    ("Check back in November", RC.TIMING),
    ("Don't reach out until Q1 please", RC.TIMING),
    ("Sounds good but reach out next month", RC.TIMING),
    # unsubscribe
    ("Please remove me from your list", RC.UNSUBSCRIBE),
    ("unsubscribe", RC.UNSUBSCRIBE),
    ("Stop emailing me.", RC.UNSUBSCRIBE),
    ("Don't contact me again", RC.UNSUBSCRIBE),
    ("Take me off your mailing list, not interested", RC.UNSUBSCRIBE),
    ("Yes, please opt me out", RC.UNSUBSCRIBE),
    ("STOP", RC.UNSUBSCRIBE),
    ("Remove.", RC.UNSUBSCRIBE),
    # referral
    ("You should speak to Jane Smith (jane.smith@acme.com), she handles this.", RC.REFERRAL),
    ("Not me - reach out to our CFO, Maria Garcia at maria@acme.com", RC.REFERRAL),
    ("Mark handles this, try him", RC.REFERRAL),
    ("Not interested but my colleague jane.doe@acme.com might be", RC.REFERRAL),
    # other
    ("Thanks for the note", RC.OTHER),
    ("Please contact us via the website", RC.OTHER),
    ("Contact me when you're in London", RC.OTHER),
    ("Let's get back on track", RC.OTHER),
    ("I'll get back to you on Monday", RC.OTHER),
    ("", RC.OTHER),
])
def test_classify_rules_categories(make_ctx, body, category):
    ctx = make_ctx(offer=OFFER)
    r = classify_rules(R(body, frm="bob@acme.com"), ctx)
    assert r.category == category, (body, r.category, r.summary)
    assert r.classifier == "rules"
    assert 0.0 <= r.confidence <= 1.0


@pytest.mark.parametrize("body", [
    "Not interested", "I'm not really interested", "no longer interested", "not interested at all",
    "We are not interested in this", "I'm not sure", "Not open to it", "not keen",
])
def test_negations_are_never_positive(make_ctx, body):
    r = classify_rules(R(body), make_ctx())
    assert r.category != RC.POSITIVE, body


def test_precedence_unsubscribe_beats_positive_and_referral(make_ctx):
    ctx = make_ctx()
    assert classify_rules(R("Yes interested, but remove me from this list"), ctx).category == RC.UNSUBSCRIBE
    assert classify_rules(R("Unsubscribe. Speak to Bob Jones bob@acme.com"), ctx).category == RC.UNSUBSCRIBE


def test_precedence_referral_beats_negative(make_ctx):
    r = classify_rules(R("Not interested myself - speak to Priya Patel, she runs finance."), make_ctx())
    assert r.category == RC.REFERRAL and r.referral_name == "Priya Patel"


def test_timing_follow_up_dates(make_ctx):
    ctx = make_ctx()
    cases = {
        "no, not right now": TODAY + timedelta(days=30),
        "Not now, try next quarter": date(2026, 10, 1),
        "Reach out next month": TODAY + timedelta(days=30),
        "Not now - reach out in 3 weeks": TODAY + timedelta(days=21),
        "Reach out in 2 months": TODAY + timedelta(days=60),
        "Maybe in a few months": TODAY + timedelta(days=90),
        "Check back after Q1": date(2027, 4, 1),
        "Check back after Q4": date(2027, 1, 1),
        "Maybe in the new year": date(2027, 1, 1),
        "reach out after October 15th": date(2026, 10, 16),
        "Check back in January": date(2027, 1, 1),
        "Not right now, try again mid-November": date(2026, 11, 15),
    }
    for body, expected in cases.items():
        r = classify_rules(R(body), ctx)
        assert r.category == RC.TIMING, body
        assert r.follow_up_date == expected.isoformat(), (body, r.follow_up_date)


def test_timing_default_days_from_playbook(make_ctx):
    ctx = make_ctx(replies={"timing_default_days": 45})
    r = classify_rules(R("Not at the moment"), ctx)
    assert r.follow_up_date == (TODAY + timedelta(days=45)).isoformat()


def test_timing_follow_up_helper_reports_explicitness():
    assert timing_follow_up("not now", TODAY) == (TODAY + timedelta(days=30), False)
    assert timing_follow_up("next quarter", TODAY) == (date(2026, 10, 1), True)
    assert timing_follow_up("in Q2 2027", TODAY) == (date(2027, 4, 1), True)
    assert timing_follow_up("after Q3", TODAY) == (date(2026, 10, 1), True)
    assert timing_follow_up("Q1 next year", TODAY) == (date(2027, 1, 1), True)
    assert timing_follow_up("in a couple of weeks", TODAY) == (TODAY + timedelta(days=14), True)
    assert timing_follow_up("later this year", TODAY) == (TODAY + timedelta(days=60), True)
    assert timing_follow_up("maybe later", TODAY, default_days=10) == (TODAY + timedelta(days=10), False)


# --- out of office ---------------------------------------------------------------

@pytest.mark.parametrize("subject,body,expected", [
    ("Automatic reply: Quick question", "I am out of the office until Monday with limited access to email.",
     "2026-09-28"),  # TODAY is a Thursday
    ("Re: hi", "I'm on annual leave, back on October 3.", "2026-10-03"),
    ("Re: hi", "Thank you for your email. I am returning 3rd October.", "2026-10-03"),
    ("Re: hi", "Out of office until 10/03/2026", "2026-10-03"),
    ("Re: hi", "I'm on vacation until 13/10, reachable after that.", "2026-10-13"),
    ("Out of Office", "I'm away. I will be back in the office on Monday, October 5th.", "2026-10-05"),
    ("Re: hi", "On parental leave, returning 5 January 2027.", "2027-01-05"),
    ("Re: hi", "Out of office. I'll be back on the 2nd.", "2026-10-02"),
    ("Re: hi", "Out of the office from September 20 to October 3.", "2026-10-03"),
    ("Re: hi", "Out of office, back 2026-10-12", "2026-10-12"),
    ("Out of office until 3 Oct", "I have limited access to email.", "2026-10-03"),
    ("Re: hi", "Auto-reply: I'm out of the office.", None),
])
def test_ooo_detection_and_return_date(make_ctx, subject, body, expected):
    r = classify_rules(R(body, subject=subject), make_ctx())
    assert r.category == RC.OOO, (body, r.category)
    assert r.follow_up_date == expected


def test_ooo_return_date_resolution_rules():
    # already-passed date without a year stays this year (stale auto-reply), far past rolls over
    assert ooo_return_date("back on September 20", TODAY) == date(2026, 9, 20)
    assert ooo_return_date("back on March 2", TODAY) == date(2027, 3, 2)
    # numeric dates are read both ways; the nearest plausible one wins
    assert ooo_return_date("until 05/06", TODAY) == date(2027, 5, 6)
    assert ooo_return_date("until 10/03/2026", TODAY) == date(2026, 10, 3)
    assert ooo_return_date("back tomorrow", TODAY) == date(2026, 9, 25)
    assert ooo_return_date("back next week", TODAY) == date(2026, 9, 28)
    assert ooo_return_date("back on Thursday", TODAY) == date(2026, 10, 1)  # next one, not today
    assert ooo_return_date("24/7 support is available", TODAY) is None
    assert ooo_return_date("nothing here", TODAY) is None


def test_weak_ooo_has_lower_confidence(make_ctx):
    ctx = make_ctx()
    weak = classify_rules(R("Travelling now, back on Monday - let's chat then"), ctx)
    strong = classify_rules(R("Out of office until Monday"), ctx)
    assert weak.category == strong.category == RC.OOO
    assert weak.confidence < 0.8 <= strong.confidence


# --- referral ----------------------------------------------------------------------

def test_referral_extraction_name_and_email(make_ctx):
    ctx = make_ctx(offer=OFFER)
    r = classify_rules(R("You should speak to Jane Smith (jane.smith@acme.com), she handles this."), ctx)
    assert (r.referral_name, r.referral_email) == ("Jane Smith", "jane.smith@acme.com")
    assert r.confidence >= 0.8 and "Jane Smith" in r.summary

    r = classify_rules(R("I'm cc'ing Bob Jones who is in charge of hiring."), ctx)
    assert (r.category, r.referral_name, r.referral_email) == (RC.REFERRAL, "Bob Jones", "")

    r = classify_rules(R("Not me - reach out to our Head of Finance, Maria Garcia <maria@acme.com>"), ctx)
    assert (r.referral_name, r.referral_email) == ("Maria Garcia", "maria@acme.com")

    r = classify_rules(R("Forwarding to accounts@acme.com, they handle this"), ctx)
    assert r.category == RC.REFERRAL and r.referral_email == "accounts@acme.com" and r.referral_name == ""

    r = classify_rules(R("The better person to talk to is Priya Patel"), ctx)
    assert r.referral_name == "Priya Patel"

    r = classify_rules(R("Please contact Sam O'Neil, he looks after this"), ctx)
    assert r.referral_name == "Sam O'Neil"


def test_referral_name_derived_from_email(make_ctx):
    r = classify_rules(R("Try reaching out to li.wei@acme.com instead"), make_ctx())
    assert (r.referral_name, r.referral_email) == ("Li Wei", "li.wei@acme.com")
    assert name_from_email("jane.smith@acme.com") == "Jane Smith"
    assert name_from_email("jsmith@acme.com") == "" and name_from_email("") == ""


def test_referral_ignores_own_sender_and_machine_addresses(make_ctx):
    ctx = make_ctx(offer=OFFER)
    # the replier's own address and our domain are never "the referral"
    r = classify_rules(R("Speak to me directly at jane@acme.com or tom@northwind.io", frm="jane@acme.com"), ctx)
    assert r.category != RC.REFERRAL
    r = classify_rules(R("Contact noreply@acme.com instead"), ctx)
    assert r.category != RC.REFERRAL


# --- bounces -------------------------------------------------------------------------

GMAIL_BOUNCE = (
    "** Address not found **\n\nYour message wasn't delivered to jane@acme.com because the address "
    "couldn't be found, or is unable to receive mail.\n\nThe response from the remote server was:\n"
    "550 5.1.1 The email account that you tried to reach does not exist.\n\n"
    "----- Original message -----\n\nFrom: Tom Sender <tom@northwind.io>\nTo: jane@acme.com\n"
    "Subject: Quick question\n")
OUTLOOK_NDR = (
    "Delivery has failed to these recipients or groups:\n\njane@acme.com (jane@acme.com)\n"
    "The email address you entered couldn't be found.\n\nDiagnostic information for administrators:\n"
    "Generating server: AM0PR01MB1234.eurprd01.prod.outlook.com\n\njane@acme.com\n"
    "Remote Server returned '550 5.1.1 RESOLVER.ADR.RecipNotFound; not found'\n")
POSTFIX_DSN = (
    "This is the mail system at host mx.northwind.io.\n\nI'm sorry to have to inform you that your "
    "message could not\nbe delivered to one or more recipients.\n\n<jane@acme.com>: host "
    "mx.acme.com[1.2.3.4] said: 550 5.1.1 <jane@acme.com>: Recipient address rejected: User unknown\n\n"
    "Reporting-MTA: dns; mx.northwind.io\nFinal-Recipient: rfc822; jane@acme.com\nAction: failed\n")


@pytest.mark.parametrize("frm,subject,body", [
    ("mailer-daemon@googlemail.com", "Delivery Status Notification (Failure)", GMAIL_BOUNCE),
    ("postmaster@outlook.com", "Undeliverable: Quick question", OUTLOOK_NDR),
    ("MAILER-DAEMON@mx.northwind.io", "Undelivered Mail Returned to Sender", POSTFIX_DSN),
    ("support@acme.com", "Undeliverable: Quick question", "Your message to jane@acme.com couldn't be delivered."),
])
def test_bounce_detection_and_address(make_ctx, frm, subject, body):
    r = classify_rules(R(body, frm=frm, subject=subject), make_ctx(offer=OFFER))
    assert r.category == RC.BOUNCE
    assert r.summary == "Bounced: jane@acme.com"
    assert r.confidence >= 0.8


def test_delay_notice_is_not_a_bounce(make_ctx):
    body = ("Delivery incomplete\nThere was a temporary problem delivering your message to "
            "jane@acme.com. Gmail will retry for 46 more hours.")
    r = classify_rules(R(body, frm="mailer-daemon@googlemail.com",
                         subject="Delivery Status Notification (Delay)"), make_ctx())
    assert r.category == RC.OTHER and "delayed" in r.summary.lower()


def test_extract_bounced_emails_priorities():
    cands = extract_bounced_emails(POSTFIX_DSN, exclude=["mailer-daemon@mx.northwind.io"])
    assert cands[0] == "jane@acme.com"
    # our own address on the From: line of the returned message is never a candidate
    assert extract_bounced_emails(GMAIL_BOUNCE)[0] == "jane@acme.com"
    assert "tom@northwind.io" not in extract_bounced_emails(GMAIL_BOUNCE)
    assert extract_bounced_emails("To: x@other.com\nhello", own_domain="other.com") == []
    assert extract_bounced_emails("") == []
    html_dsn = "<p>Your message to <b>jane@acme.com</b> couldn&#39;t be delivered.</p>"
    assert extract_bounced_emails(html_dsn) == ["jane@acme.com"]


# --- regressions: our own subject line / people *mentioning* delivery problems -------

def test_our_subject_words_do_not_trigger_ooo_or_bounce(make_ctx):
    ctx = make_ctx()
    r = classify_rules(R("Sounds good, let's chat", subject="Re: Holiday hiring push - out of office cover"), ctx)
    assert r.category == RC.POSITIVE
    r = classify_rules(R("Interested, tell me more", subject="Re: Your emails are undeliverable"), ctx)
    assert r.category == RC.POSITIVE
    r = classify_rules(R("ok", subject="RE: unsubscribe links that work"), ctx)
    assert r.category != RC.UNSUBSCRIBE
    # ... but a marker the replying system put *before* the "Re:" counts
    r = classify_rules(R("I'll be back soon.", subject="Automatic reply: Re: Holiday hiring push"), ctx)
    assert r.category == RC.OOO


def test_person_mentioning_undeliverable_is_not_a_bounce(make_ctx):
    ctx = make_ctx()
    r = classify_rules(R("We already have a tool that flags undeliverable addresses. Not interested."), ctx)
    assert r.category == RC.NEGATIVE
    r = classify_rules(R("Your message could not be delivered to jane@acme.com", frm="no-reply@acme.com"), ctx)
    assert r.category == RC.BOUNCE and r.summary == "Bounced: jane@acme.com"


@pytest.mark.parametrize("body,category,name,follow_up", [
    ("Can you send it back on Monday?", RC.QUESTION, "", None),
    ("Please return the form on Monday", RC.OTHER, "", None),
    ("Please speak to Jane's team", RC.REFERRAL, "Jane", None),
    ("We are good to go", RC.OTHER, "", None),
    ("No need to apologise - happy to chat", RC.POSITIVE, "", None),
    ("Not now, maybe in 2.5 weeks", RC.TIMING, "", "2026-10-24"),  # "2.5" is not a date
])
def test_rule_edge_cases(make_ctx, body, category, name, follow_up):
    r = classify_rules(R(body), make_ctx())
    assert (r.category, r.referral_name, r.follow_up_date) == (category, name, follow_up), r.summary


def test_iso_datetime_and_bad_config_values(make_ctx):
    assert ooo_return_date("Out of office, back 2026-10-12T09:00", TODAY) == date(2026, 10, 12)
    ctx = make_ctx(replies={"timing_default_days": "thirty", "ooo_default_days": 0})
    r = classify_rules(R("Not at the moment"), ctx)
    assert r.follow_up_date == (TODAY + timedelta(days=30)).isoformat()
    seed_lead(ctx)
    handle_reply(R("Auto-reply: out of the office"), ctx)
    assert ctx.store.due_followups(TODAY + timedelta(days=7), "test")[0]["due"] == \
        (TODAY + timedelta(days=7)).isoformat()


# =============================================================================
# classify_ai
# =============================================================================

def ai_json(**kw):
    base = {"category": "positive", "confidence": 0.92, "summary": "Wants a call next week.",
            "referral_name": "", "referral_email": "", "follow_up_date": None,
            "suggested_reply": "Hi Jane, great - here's my calendar: https://cal.com/tom/intro"}
    base.update(kw)
    return base


def test_classify_ai_happy_path_and_prompt(make_ctx):
    ctx = make_ctx(offer=OFFER)
    ctx.llm = FakeLLM(ai_json())
    body = "Could work - send me a couple of times.\n\nOn Tue, Tom <tom@northwind.io> wrote:\n> SECRET QUOTE"
    r = classify_ai(R(body), ctx)
    assert (r.category, r.classifier, r.confidence) == (RC.POSITIVE, "ai", 0.92)
    assert r.summary == "Wants a call next week."
    assert r.suggested_reply.startswith("Hi Jane")
    call = ctx.llm.calls[0]
    assert call["system"] == AI_SYSTEM_PROMPT and call["json_mode"] is True
    for cat in RC.ALL:
        assert f'"{cat}"' in call["system"]
    user = call["user"]
    assert "2026-09-24" in user and "Thursday" in user
    assert "Northwind Talent" in user and "finance recruitment" in user and "https://cal.com/tom/intro" in user
    assert "send me a couple of times" in user and "SECRET QUOTE" not in user
    assert "jane@acme.com" in user and "Re: Quick question" in user


def test_classify_ai_code_fenced_json_alias_and_confidence_scale(make_ctx):
    ctx = make_ctx()
    ctx.llm = FakeLLM("Sure:\n```json\n" + json.dumps(ai_json(category="Out_Of_Office", confidence=85,
                                                               follow_up_date="2026-10-05")) + "\n```")
    r = classify_ai(R("I'm away"), ctx)
    assert r.category == RC.OOO and r.confidence == 0.85 and r.follow_up_date == "2026-10-05"


def test_classify_ai_referral_fields(make_ctx):
    ctx = make_ctx()
    ctx.llm = FakeLLM(ai_json(category="referral", referral_name="Bob Jones",
                              referral_email="Bob.Jones@Acme.com"))
    r = classify_ai(R("Please talk to Bob"), ctx)
    assert (r.category, r.referral_name, r.referral_email) == (RC.REFERRAL, "Bob Jones", "bob.jones@acme.com")


def test_classify_ai_fills_missing_details_with_rules(make_ctx):
    ctx = make_ctx()
    ctx.llm = FakeLLM(ai_json(category="timing", follow_up_date=None, suggested_reply=""),
                      ai_json(category="referral", referral_name="null", referral_email="not-an-email"),
                      ai_json(category="ooo", follow_up_date="null"))
    r = classify_ai(R("Not now - try us next quarter"), ctx)
    assert r.category == RC.TIMING and r.follow_up_date == "2026-10-01"
    r = classify_ai(R("Speak to Priya Patel (priya@acme.com) instead"), ctx)
    assert r.category == RC.REFERRAL and r.referral_email == "priya@acme.com" and r.referral_name == "Priya Patel"
    r = classify_ai(R("Out of office, back on October 7"), ctx)
    assert r.category == RC.OOO and r.follow_up_date == "2026-10-07"


def test_classify_ai_drops_dates_for_other_categories(make_ctx):
    ctx = make_ctx()
    ctx.llm = FakeLLM(ai_json(category="positive", follow_up_date="2026-10-01", referral_email="x@y.com"))
    r = classify_ai(R("yes let's talk"), ctx)
    assert r.follow_up_date is None and r.referral_email == ""


@pytest.mark.parametrize("response", [
    ai_json(category="maybe"),                       # not a category
    ai_json(category=None),
    ai_json(follow_up_date="next tuesday"),          # bad date format
    ai_json(follow_up_date="2026-02-30"),            # impossible date
    ai_json(follow_up_date="2026-10-01T00:00:00"),
    "I think this is positive",                      # not JSON
    LLMError("rate limited"),                        # client error
    RuntimeError("network down"),
])
def test_classify_ai_falls_back_to_rules(make_ctx, response):
    ctx = make_ctx()
    ctx.llm = FakeLLM(response)
    r = classify_ai(R("Not interested, thanks"), ctx)
    assert r.classifier == "rules" and r.category == RC.NEGATIVE


def test_classify_ai_non_dict_json_falls_back(make_ctx):
    class ListLLM(FakeLLM):
        def complete_json(self, system, user, **kw):
            self.calls.append({"system": system, "user": user})
            return ["positive"]

    ctx = make_ctx()
    ctx.llm = ListLLM()
    assert classify_ai(R("No thanks"), ctx).classifier == "rules"


def test_classify_ai_without_llm_or_in_dry_run_uses_rules(make_ctx):
    ctx = make_ctx()
    ctx.llm = None
    assert classify_ai(R("yes please"), ctx).classifier == "rules"
    ctx = make_ctx(dry_run=True)
    llm = FakeLLM(ai_json())
    ctx.llm = llm
    assert classify_ai(R("yes please"), ctx).classifier == "rules"
    assert llm.calls == []


def test_classify_ai_resets_stale_fields(make_ctx):
    ctx = make_ctx()
    ctx.llm = FakeLLM(ai_json(category="negative", suggested_reply=""))
    reply = R("no", referral_email="old@x.com", follow_up_date="2020-01-01", suggested_reply="old")
    classify_ai(reply, ctx)
    assert reply.referral_email == "" and reply.follow_up_date is None and reply.suggested_reply == ""


# =============================================================================
# classify (mode dispatch)
# =============================================================================

def test_classify_rules_mode_never_calls_llm(make_ctx):
    ctx = make_ctx(replies={"classifier": "rules"})
    ctx.llm = FakeLLM(ai_json())
    assert classify(R("yes"), ctx).classifier == "rules"
    assert ctx.llm.calls == []


def test_classify_auto_uses_rules_for_machine_mail(make_ctx):
    ctx = make_ctx()  # classifier defaults to auto
    llm = FakeLLM(ai_json())
    ctx.llm = llm
    ooo = classify(R("Out of office until Monday", subject="Automatic reply: hi"), ctx)
    bounce = classify(R(GMAIL_BOUNCE, frm="mailer-daemon@googlemail.com", subject="Delivery Status Notification"), ctx)
    unsub = classify(R("Please unsubscribe me"), ctx)
    delay = classify(R("Delivery delayed, will retry", frm="mailer-daemon@googlemail.com"), ctx)
    assert (ooo.category, bounce.category, unsub.category, delay.category) == (RC.OOO, RC.BOUNCE, RC.UNSUBSCRIBE, RC.OTHER)
    assert {ooo.classifier, bounce.classifier, unsub.classifier, delay.classifier} == {"rules"}
    assert ooo.follow_up_date == "2026-09-28"
    assert llm.calls == []


def test_classify_auto_sends_human_replies_to_ai(make_ctx):
    ctx = make_ctx()
    ctx.llm = FakeLLM(ai_json(), ai_json(category="positive"))
    assert classify(R("Could be interesting, what does it cost?"), ctx).classifier == "ai"
    # a weak (low-confidence) OOO guess goes to the AI too
    assert classify(R("Travelling now, back on Monday - let's chat then"), ctx).classifier == "ai"
    assert len(ctx.llm.calls) == 2


def test_classify_auto_without_llm_or_dry_run(make_ctx):
    ctx = make_ctx()
    ctx.llm = None
    assert classify(R("yes please"), ctx).classifier == "rules"
    ctx = make_ctx(dry_run=True)
    llm = FakeLLM(ai_json())
    ctx.llm = llm
    r = classify(R("yes please"), ctx)
    assert r.classifier == "rules" and r.category == RC.POSITIVE and llm.calls == []


def test_classify_ai_mode(make_ctx):
    ctx = make_ctx(replies={"classifier": "ai"})
    ctx.llm = FakeLLM(ai_json(category="ooo", follow_up_date="2026-10-02"))
    r = classify(R("Out of office"), ctx)
    assert r.classifier == "ai" and r.follow_up_date == "2026-10-02"
    ctx = make_ctx(replies={"classifier": "ai"})
    ctx.llm = None
    assert classify(R("Out of office"), ctx).classifier == "rules"


def test_classify_survives_broken_llm_config(make_ctx, monkeypatch):
    import leadgen.llm

    def boom(ctx):
        raise RuntimeError("bad llm config")

    monkeypatch.setattr(leadgen.llm, "build_llm", boom)
    ctx = make_ctx()  # ctx.llm not set: first access calls build_llm
    r = classify(R("Sounds good"), ctx)
    assert r.classifier == "rules" and r.category == RC.POSITIVE


def test_classify_unknown_mode_falls_back_to_rules(make_ctx):
    ctx = make_ctx()
    ctx.playbook.replies["classifier"] = "magic"
    ctx.llm = FakeLLM(ai_json())
    assert classify(R("yes"), ctx).classifier == "rules"


def test_classify_returns_same_object(make_ctx):
    ctx = make_ctx()
    ctx.llm = None
    reply = R("Out of office until Monday")
    assert classify(reply, ctx) is reply and reply.category == RC.OOO


# =============================================================================
# suggest_reply
# =============================================================================

def test_suggest_reply_positive(make_ctx):
    ctx = make_ctx(offer=OFFER)
    lead = seed_lead(ctx)
    text = suggest_reply(Reply(from_email="jane@acme.com", body="yes", category=RC.POSITIVE), lead, ctx)
    assert text.startswith("Hi Jane,") and "https://cal.com/tom/intro" in text and text.endswith("Tom Sender")
    ctx2 = make_ctx()
    text = suggest_reply(Reply(from_email="x@acme.com", body="yes", category=RC.POSITIVE), None, ctx2)
    assert text.startswith("Hi there,") and "What time works" in text and "this week" in text


def test_suggest_reply_question_referral_timing(make_ctx):
    ctx = make_ctx(offer=OFFER)
    lead = seed_lead(ctx)
    q = suggest_reply(Reply(from_email="jane@acme.com", body="?", category=RC.QUESTION), lead, ctx)
    assert "[short answer" in q and "https://cal.com/tom/intro" in q
    ref = suggest_reply(Reply(from_email="jane@acme.com", body="", category=RC.REFERRAL,
                              referral_name="Bob Jones", referral_email="bob@acme.com"), lead, ctx)
    assert ref.startswith("Hi Bob,") and "Jane Doe suggested I get in touch" in ref
    assert "finance recruitment" in ref and "https://cal.com/tom/intro" in ref
    t = suggest_reply(Reply(from_email="jane@acme.com", body="", category=RC.TIMING,
                            follow_up_date="2026-10-01"), lead, ctx)
    assert "reach out around October 1." in t
    t2 = suggest_reply(Reply(from_email="jane@acme.com", body="", category=RC.TIMING,
                             follow_up_date="2027-01-01"), lead, ctx)
    assert "January 1, 2027" in t2


def test_suggest_reply_other_categories_and_ai_text(make_ctx):
    ctx = make_ctx(offer=OFFER)
    for cat in (RC.NEGATIVE, RC.UNSUBSCRIBE, RC.OOO, RC.BOUNCE, RC.OTHER):
        assert suggest_reply(Reply(from_email="a@b.com", body="", category=cat), None, ctx) == ""
    ai = Reply(from_email="a@b.com", body="", category=RC.POSITIVE, suggested_reply="  AI draft  ")
    assert suggest_reply(ai, None, ctx) == "AI draft"


def test_suggest_reply_uses_signature(make_ctx):
    ctx = make_ctx(offer={"signature": "Cheers,\nTom | Northwind", "sender_name": "Tom"})
    text = suggest_reply(Reply(from_email="jane.doe@acme.com", body="", category=RC.POSITIVE), None, ctx)
    assert text.startswith("Hi Jane,") and text.endswith("Cheers,\nTom | Northwind")


# =============================================================================
# handle_reply
# =============================================================================

def test_handle_positive(make_ctx, capsys):
    ctx = make_ctx(offer=OFFER, replies={"classifier": "rules"})
    lead = seed_lead(ctx)
    body = "Sounds good - send me some times.\n\nOn Wed, Tom <tom@northwind.io> wrote:\n> Hi Jane"
    r = handle_reply(R(body, frm="Jane@Acme.com", received_at="2026-09-24T10:00:00Z"), ctx)
    assert r.category == RC.POSITIVE and r.lead_id == lead.id
    assert stage_of(ctx, lead) == Stage.POSITIVE
    assert r.body == "Sounds good - send me some times."
    assert "https://cal.com/tom/intro" in r.suggested_reply
    assert "lead stage -> positive" in r.action and "alerted 1 channel(s)" in r.action
    out = capsys.readouterr().out
    assert "[POSITIVE] Positive reply: Jane Doe (Acme)" in out
    assert "Booking link: https://cal.com/tom/intro" in out and "Sounds good - send me some times." in out
    saved = ctx.store.list_replies("test")
    assert len(saved) == 1 and saved[0].category == RC.POSITIVE and saved[0].lead_id == lead.id
    assert saved[0].body == "Sounds good - send me some times." and saved[0].action == r.action
    assert ctx.store.funnel("test")["stages"]["replied"] == 1


def test_handle_question_and_other(make_ctx):
    ctx = make_ctx(replies={"classifier": "rules"})
    lead = seed_lead(ctx)
    r = handle_reply(R("How much does this cost?"), ctx)
    assert r.category == RC.QUESTION and stage_of(ctx, lead) == Stage.REPLIED
    lead2 = seed_lead(ctx, email="sam@beta.com", name="Sam Lee", company="Beta", domain="beta.com")
    r = handle_reply(R("Thanks for the note", frm="sam@beta.com"), ctx)
    assert r.category == RC.OTHER and stage_of(ctx, lead2) == Stage.REPLIED


def test_handle_referral_creates_lead(make_ctx):
    ctx = make_ctx(offer=OFFER, replies={"classifier": "rules"})
    lead = seed_lead(ctx)
    r = handle_reply(R("Not my area - speak to Bob Jones (bob.jones@acme.com), he handles this."), ctx)
    assert r.category == RC.REFERRAL and stage_of(ctx, lead) == Stage.REPLIED
    new = ctx.store.find_lead_by_email("bob.jones@acme.com", "test")
    assert new is not None and new.id != lead.id
    assert new.stage == Stage.QUALIFIED and new.company.name == "Acme" and new.company.domain == "acme.com"
    assert new.contact.full_name == "Bob Jones" and new.contact.source == "referral"
    assert new.notes == ["referred by jane@acme.com"] and new.playbook == "test"
    assert new.score == lead.score and new.tier == lead.tier
    assert f"created lead {new.id}" in r.action
    assert r.suggested_reply.startswith("Hi Bob,") and "Jane Doe suggested" in r.suggested_reply


def test_handle_referral_skips_suppressed_existing_and_nameless(make_ctx):
    ctx = make_ctx(replies={"classifier": "rules"})
    seed_lead(ctx)
    ctx.store.suppress("bob@acme.com", "email", "competitor")
    r = handle_reply(R("Speak to Bob Jones at bob@acme.com"), ctx)
    assert "suppressed - not added" in r.action
    assert ctx.store.find_lead_by_email("bob@acme.com", "test") is None
    seed_lead(ctx, email="kim@acme.com", name="Kim Ray")
    r = handle_reply(R("Please reach out to Kim Ray, kim@acme.com"), ctx)
    assert "already a lead" in r.action
    r = handle_reply(R("I'm cc'ing Priya Patel who is in charge of this."), ctx)
    assert r.category == RC.REFERRAL and "no address given" in r.action


def test_handle_referral_from_unknown_sender(make_ctx):
    ctx = make_ctx(replies={"classifier": "rules"})
    r = handle_reply(R("Reach out to Ana Silva at ana.silva@gamma.io", frm="someone@gamma.io"), ctx)
    new = ctx.store.find_lead_by_email("ana.silva@gamma.io", "test")
    assert r.lead_id == "" and "no matching lead" in r.action
    assert new.company.domain == "gamma.io" and new.company.name == "gamma.io"


def test_handle_timing_schedules_followup(make_ctx):
    ctx = make_ctx(replies={"classifier": "rules"})
    lead = seed_lead(ctx)
    r = handle_reply(R("Not right now - try us next quarter"), ctx)
    assert r.category == RC.TIMING and r.follow_up_date == "2026-10-01"
    assert stage_of(ctx, lead) == Stage.REPLIED
    assert ctx.store.due_followups(date(2026, 9, 30), "test") == []
    due = ctx.store.due_followups(date(2026, 10, 1), "test")
    assert len(due) == 1 and due[0]["reason"] == "timing" and due[0]["lead_id"] == lead.id
    assert due[0]["email"] == "jane@acme.com"
    assert "follow-up scheduled 2026-10-01 (timing)" in r.action
    assert "October 1" in r.suggested_reply


def test_handle_timing_default_and_past_dates(make_ctx):
    ctx = make_ctx(replies={"classifier": "ai", "timing_default_days": 14})
    seed_lead(ctx)
    ctx.llm = FakeLLM(ai_json(category="timing", follow_up_date=None, suggested_reply=""),
                      ai_json(category="timing", follow_up_date="2026-01-01", suggested_reply=""))
    r = handle_reply(R("Not at the moment"), ctx)
    assert r.follow_up_date == (TODAY + timedelta(days=14)).isoformat()
    r = handle_reply(R("Not now, maybe later"), ctx)
    assert r.follow_up_date == (TODAY + timedelta(days=1)).isoformat()  # never schedule in the past


def test_handle_ooo(make_ctx):
    ctx = make_ctx(replies={"classifier": "rules"})
    lead = seed_lead(ctx)
    r = handle_reply(R("I'm on annual leave, back on October 3.", subject="Automatic reply: hi"), ctx)
    assert r.category == RC.OOO and r.follow_up_date == "2026-10-03"
    assert stage_of(ctx, lead) == Stage.EXPORTED  # no stage change
    due = ctx.store.due_followups(date(2026, 10, 4), "test")
    assert [(d["due"], d["reason"]) for d in due] == [("2026-10-04", "ooo")]
    assert r.suggested_reply == ""


def test_handle_ooo_without_date(make_ctx):
    ctx = make_ctx(replies={"classifier": "rules", "ooo_default_days": 10})
    seed_lead(ctx)
    r = handle_reply(R("Auto-reply: I'm out of the office with limited access to email."), ctx)
    assert r.follow_up_date is None
    due = ctx.store.due_followups(TODAY + timedelta(days=30), "test")
    assert due[0]["due"] == (TODAY + timedelta(days=10)).isoformat()
    assert "no return date" in r.action


def test_handle_negative_and_unsubscribe(make_ctx):
    ctx = make_ctx(replies={"classifier": "rules"})
    lead = seed_lead(ctx)
    r = handle_reply(R("Not interested, thanks"), ctx)
    assert r.category == RC.NEGATIVE and stage_of(ctx, lead) == Stage.LOST
    assert ctx.store.is_suppressed(email="jane@acme.com")
    assert ctx.store.list_suppressed()[0]["reason"] == "not interested"
    assert ctx.store.funnel("test")["stages"]["replied"] == 1  # the reply still counts in the funnel

    lead2 = seed_lead(ctx, email="sam@beta.com", name="Sam Lee", company="Beta", domain="beta.com")
    r = handle_reply(R("Please remove me from your list", frm="sam@beta.com"), ctx)
    assert r.category == RC.UNSUBSCRIBE and stage_of(ctx, lead2) == Stage.LOST
    reasons = {s["value"]: s["reason"] for s in ctx.store.list_suppressed()}
    assert reasons["sam@beta.com"] == "unsubscribed"


def test_handle_bounce_uses_address_from_notice(make_ctx):
    ctx = make_ctx(offer=OFFER, replies={"classifier": "rules"})
    lead = seed_lead(ctx)
    r = handle_reply(R(GMAIL_BOUNCE, frm="mailer-daemon@googlemail.com",
                       subject="Delivery Status Notification (Failure)"), ctx)
    assert r.category == RC.BOUNCE and r.lead_id == lead.id
    assert stage_of(ctx, lead) == Stage.LOST
    assert ctx.store.is_suppressed(email="jane@acme.com")
    assert not ctx.store.is_suppressed(email="mailer-daemon@googlemail.com")
    assert not ctx.store.is_suppressed(email="tom@northwind.io")
    assert ctx.store.get_verification("jane@acme.com", today=date.today()) == "invalid"
    assert "marked jane@acme.com invalid" in r.action


def test_handle_bounce_prefers_known_lead_address(make_ctx):
    ctx = make_ctx(replies={"classifier": "rules"})
    lead = seed_lead(ctx, email="kim@acme.com", name="Kim Ray")
    body = ("Your message could not be delivered.\nFinal-Recipient: rfc822; old@acme.com\n\n"
            "Original message\nTo: kim@acme.com\n")
    r = handle_reply(R(body, frm="postmaster@acme.com", subject="Undeliverable"), ctx)
    assert r.lead_id == lead.id and stage_of(ctx, lead) == Stage.LOST
    assert ctx.store.is_suppressed(email="kim@acme.com")


def test_handle_bounce_without_address(make_ctx):
    ctx = make_ctx(replies={"classifier": "rules"})
    r = handle_reply(R("Undeliverable message", frm="mailer-daemon@x.com", subject="Undeliverable"), ctx)
    assert r.category == RC.BOUNCE and "nothing suppressed" in r.action
    assert ctx.store.list_suppressed() == []


def test_handle_delay_notice_changes_nothing(make_ctx):
    ctx = make_ctx(replies={"classifier": "rules"})
    lead = seed_lead(ctx)
    r = handle_reply(R("Delivery incomplete. There was a temporary problem delivering your message to "
                       "jane@acme.com. Gmail will retry.", frm="mailer-daemon@googlemail.com",
                       subject="Delivery Status Notification (Delay)"), ctx)
    assert r.category == RC.OTHER and "no action" in r.action
    assert stage_of(ctx, lead) == Stage.EXPORTED and ctx.store.list_suppressed() == []


def test_handle_unknown_sender_still_saves_and_suppresses(make_ctx):
    ctx = make_ctx(replies={"classifier": "rules"})
    r = handle_reply(R("No thanks", frm="stranger@nowhere.com"), ctx)
    assert r.lead_id == "" and r.category == RC.NEGATIVE and "no matching lead" in r.action
    assert ctx.store.is_suppressed(email="stranger@nowhere.com")
    assert len(ctx.store.list_replies("test")) == 1


def test_handle_links_by_domain_but_not_personal_domains(make_ctx):
    ctx = make_ctx(replies={"classifier": "rules"})
    lead = seed_lead(ctx)
    r = handle_reply(R("Jane forwarded me your note - interested, send over details", frm="ceo@acme.com"), ctx)
    assert r.lead_id == lead.id and stage_of(ctx, lead) == Stage.POSITIVE
    gm = seed_lead(ctx, email="pat@gmail.com", name="Pat", company="Pat's Bakery", domain="patsbakery.com")
    r = handle_reply(R("yes interested", frm="random@gmail.com"), ctx)
    assert r.lead_id == "" and stage_of(ctx, gm) == Stage.EXPORTED


def test_handle_uses_given_lead_id(make_ctx):
    ctx = make_ctx(replies={"classifier": "rules"})
    lead = seed_lead(ctx)
    r = handle_reply(R("Yes, interested", frm="assistant@otherdomain.com", lead_id=lead.id), ctx)
    assert r.lead_id == lead.id and stage_of(ctx, lead) == Stage.POSITIVE


def test_handle_duplicate_is_skipped(make_ctx):
    ctx = make_ctx(replies={"classifier": "rules"})
    seed_lead(ctx)
    first = handle_reply(R("Not right now", received_at="2026-09-24T09:00:00Z"), ctx)
    again = handle_reply(R("Not right now", received_at="2026-09-24T09:00:00Z"), ctx)
    assert again.action.startswith("duplicate") and again.category == first.category
    assert len(ctx.store.list_replies("test")) == 1
    assert len(ctx.store.due_followups(date(2027, 1, 1), "test")) == 1
    # same sender + time but a different body is a different reply
    handle_reply(R("Actually, yes - let's talk", received_at="2026-09-24T09:00:00Z"), ctx)
    assert len(ctx.store.list_replies("test")) == 2


def test_handle_alerts_respect_notify_on_and_webhook(make_ctx):
    ctx = make_ctx(offer=OFFER, replies={"classifier": "rules"},
                   notify={"channels": [{"type": "webhook", "url": HOOK_URL}], "on": ["positive", "timing"]})
    ctx.http.add("POST", HOOK_URL, json={"ok": True})
    lead = seed_lead(ctx)
    handle_reply(R("Yes - let's chat"), ctx)
    handle_reply(R("How much?"), ctx)             # question not in notify.on -> no alert
    handle_reply(R("Not now, next quarter"), ctx)
    events = [c["json"]["event"] for c in ctx.http.calls]
    assert events == ["positive", "timing"]
    payload = ctx.http.calls[0]["json"]
    assert payload["title"] == "Positive reply: Jane Doe (Acme)"
    assert payload["data"]["lead_id"] == lead.id and payload["data"]["booking_link"] == OFFER["booking_link"]
    assert payload["data"]["reply"]["category"] == "positive"
    assert "Lead score: 82 (hot)" in payload["text"]


def test_handle_ai_classifier_end_to_end(make_ctx):
    ctx = make_ctx(offer=OFFER)  # auto mode + LLM available
    lead = seed_lead(ctx)
    ctx.llm = FakeLLM(ai_json(suggested_reply="Hi Jane - here's my link: https://cal.com/tom/intro"))
    r = handle_reply(R("Could be a fit. Send times?"), ctx)
    assert r.classifier == "ai" and r.category == RC.POSITIVE
    assert r.suggested_reply == "Hi Jane - here's my link: https://cal.com/tom/intro"
    assert stage_of(ctx, lead) == Stage.POSITIVE


def test_handle_requires_store(make_ctx):
    ctx = make_ctx()
    ctx.store.close()
    ctx.store = None
    with pytest.raises(RuntimeError, match="store"):
        handle_reply(R("yes"), ctx)
    from leadgen.store import Store
    ctx.store = Store(":memory:")  # let the fixture close something valid


def test_handle_empty_body_keeps_raw_notice(make_ctx):
    ctx = make_ctx(replies={"classifier": "rules"})
    r = handle_reply(R("> quoted only", frm="x@y.com"), ctx)
    assert r.category == RC.OTHER and r.body == "> quoted only"


# =============================================================================
# load_replies_csv
# =============================================================================

def test_load_replies_csv(tmp_path):
    p = tmp_path / "replies.csv"
    p.write_text(
        "﻿From,Subject,Body,Date,Lead ID\n"
        "Jane Doe <Jane@Acme.com>,Re: hi,\"Sounds good, send times\",2026-09-24,\n"
        "bob@beta.com,Re: hi,\"Not now\nmaybe Q1\",2026-09-23,abc123\n"
        ",Re: hi,no sender,2026-09-22,\n"
        "carl@gamma.com,,,2026-09-22,\n"
        ",Re: x,has lead id only,,lead42\n", encoding="utf-8")
    rows = load_replies_csv(p)
    assert [r.from_email for r in rows] == ["jane@acme.com", "bob@beta.com", ""]
    assert rows[0].body == "Sounds good, send times" and rows[0].subject == "Re: hi"
    assert rows[0].received_at == "2026-09-24" and rows[1].lead_id == "abc123"
    assert rows[1].body == "Not now\nmaybe Q1" and rows[2].lead_id == "lead42"


@pytest.mark.parametrize("header,row", [
    ("from_email,text,received_at", "a@b.com,hello,2026-01-01"),
    ("EMAIL,MESSAGE,TIMESTAMP", "a@b.com,hello,2026-01-01"),
    ("Sender,Content,Received At", "a@b.com,hello,2026-01-01"),
    ("from-email,reply,date", "a@b.com,hello,2026-01-01"),
])
def test_load_replies_csv_header_variants(tmp_path, header, row):
    p = tmp_path / "r.csv"
    p.write_text(f"{header}\n{row}\n", encoding="utf-8")
    rows = load_replies_csv(p)
    assert len(rows) == 1 and rows[0].from_email == "a@b.com" and rows[0].body == "hello"
    assert rows[0].received_at == "2026-01-01"


def test_load_replies_tsv_and_errors(tmp_path):
    p = tmp_path / "r.tsv"
    p.write_text("email\tbody\na@b.com\thi, there\n", encoding="utf-8")
    assert load_replies_csv(p)[0].body == "hi, there"
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    assert load_replies_csv(empty) == []
    with pytest.raises(FileNotFoundError):
        load_replies_csv(tmp_path / "missing.csv")


# =============================================================================
# parse_webhook_payload
# =============================================================================

INSTANTLY_REPLY = {
    "timestamp": "2026-09-24T10:15:00.000Z",
    "event_type": "reply_received",
    "workspace": "0a1b2c3d-1111-2222-3333-444455556666",
    "campaign_id": "7f3e9a10-aaaa-bbbb-cccc-ddddeeeeffff",
    "campaign_name": "Finance hiring signal",
    "lead_email": "Jane@Acme.com",
    "email_account": "tom@northwind.io",
    "unibox_url": "https://app.instantly.ai/app/unibox?thread_search=...",
    "step": 1, "variant": 1, "is_first": True,
    "email_id": "0199a7f0-0000-7000-8000-000000000001",
    "reply_subject": "Re: Quick question about your finance hire",
    "reply_text": "Sounds good, send me some times.\n\nOn Wed, Sep 23, 2026 Tom wrote:\n> Hi Jane",
    "reply_html": "<div>Sounds good, send me some times.</div><blockquote>Hi Jane</blockquote>",
    "reply_text_snippet": "Sounds good, send me some times.",
}
SMARTLEAD_REPLY = {
    "webhook_id": 12345, "webhook_name": "Replies",
    "sl_email_lead_id": 987654, "sl_email_lead_map_id": 1234567,
    "sl_lead_email": "jane@acme.com",
    "stats_id": "abc-123",
    "from_email": "tom@northwind.io",
    "to_email": "jane@acme.com", "to_name": "Jane Doe",
    "subject": "Re: Quick question",
    "sent_message": {"message_id": "<a@northwind.io>", "html": "<p>Hi Jane</p>", "text": "Hi Jane",
                     "time": "2026-09-22T09:00:00.000Z"},
    "reply_message": {"message_id": "<c@acme.com>", "html": "<div>Not interested, thanks</div>",
                      "text": "Not interested, thanks", "time": "2026-09-24T11:00:00.000Z"},
    "reply_body": "<div>Not interested, thanks</div>",
    "preview_text": "Not interested, thanks",
    "campaign_name": "Acme", "campaign_id": 4321, "campaign_status": "ACTIVE",
    "sequence_number": 1, "secret_key": "s3cr3t",
    "app_url": "https://app.smartlead.ai/app/master-inbox",
    "event_timestamp": "2026-09-24T11:00:05.000Z",
    "event_type": "EMAIL_REPLY",
    "leadCorrespondence": {"targetLeadEmail": "jane@acme.com", "replyReceivedFrom": "jane@acme.com",
                           "repliedCompanyDomain": "acme.com"},
}


def test_parse_instantly_reply():
    r = parse_webhook_payload(INSTANTLY_REPLY)
    assert r.from_email == "jane@acme.com"
    assert r.subject == "Re: Quick question about your finance hire"
    assert r.body.startswith("Sounds good, send me some times.")
    assert r.received_at == "2026-09-24T10:15:00.000Z" and r.lead_id == ""


def test_parse_instantly_html_or_snippet_only():
    p = {k: v for k, v in INSTANTLY_REPLY.items() if k != "reply_text"}
    assert clean_reply_text(parse_webhook_payload(p).body) == "Sounds good, send me some times."
    p = {k: v for k, v in p.items() if k != "reply_html"}
    assert parse_webhook_payload(p).body == "Sounds good, send me some times."
    p = {"event_type": "reply_received", "lead_email": "a@b.com", "reply_snippet": "yes"}
    assert parse_webhook_payload(p).body == "yes"
    p = {"event_type": "auto_reply_received", "lead_email": "a@b.com", "reply_text": "Out of office"}
    assert parse_webhook_payload(p).body == "Out of office"


def test_parse_smartlead_reply():
    r = parse_webhook_payload(SMARTLEAD_REPLY)
    assert r.from_email == "jane@acme.com"  # the lead, not our sending mailbox (from_email)
    assert r.body == "Not interested, thanks" and r.subject == "Re: Quick question"
    assert r.received_at == "2026-09-24T11:00:05.000Z"
    minimal = {"event_type": "EMAIL_REPLY", "to_email": "bob@beta.com", "reply_body": "<p>Tell me more</p>",
               "time_replied": "2026-09-24T12:00:00Z"}
    r = parse_webhook_payload(minimal)
    assert (r.from_email, r.received_at) == ("bob@beta.com", "2026-09-24T12:00:00Z")
    assert clean_reply_text(r.body) == "Tell me more"
    html_only = dict(SMARTLEAD_REPLY, reply_message={"html": "<div>Yes please</div>"})
    assert clean_reply_text(parse_webhook_payload(html_only).body) == "Yes please"


def test_parse_generic_shapes():
    r = parse_webhook_payload({"from_email": "A@B.com", "body": "hi", "subject": "Re: x",
                               "received_at": "2026-09-24", "lead_id": "abc"})
    assert (r.from_email, r.body, r.subject, r.received_at, r.lead_id) == ("a@b.com", "hi", "Re: x", "2026-09-24", "abc")
    r = parse_webhook_payload({"email": "a@b.com", "text": "hello", "timestamp": 1790000000})
    assert r.body == "hello" and r.received_at.startswith("2026-")
    r = parse_webhook_payload({"from": "Jane Doe <jane@acme.com>", "message": {"text": "yo", "html": "<b>yo</b>"}})
    assert (r.from_email, r.body) == ("jane@acme.com", "yo")
    r = parse_webhook_payload({"event": "reply", "data": {"sender": "a@b.com", "content": "wrapped"}})
    assert r.body == "wrapped" and r.from_email == "a@b.com"
    r = parse_webhook_payload({"timestamp": 1790000000000, "data": dict(INSTANTLY_REPLY, timestamp=None)})
    assert r.from_email == "jane@acme.com"


def test_parse_non_reply_events_and_junk():
    for event in ("email_sent", "email_opened", "link_clicked", "lead_interested", "campaign_completed"):
        assert parse_webhook_payload(dict(INSTANTLY_REPLY, event_type=event)) is None
    for event in ("EMAIL_SENT", "EMAIL_OPEN", "EMAIL_LINK_CLICK", "LEAD_CATEGORY_UPDATED"):
        assert parse_webhook_payload(dict(SMARTLEAD_REPLY, event_type=event)) is None
    for junk in (None, [], "text", 42, {}, {"event_type": "reply_received"},
                 {"event_type": "reply_received", "lead_email": "not-an-email", "reply_text": "hi"},
                 {"event_type": "reply_received", "lead_email": "a@b.com", "reply_text": "  "},
                 {"from_email": "a@b.com"}, {"body": "hi"}):
        assert parse_webhook_payload(junk) is None, junk


def test_parse_bounce_and_unsubscribe_events(make_ctx):
    ctx = make_ctx(replies={"classifier": "rules"})
    lead = seed_lead(ctx)
    bounce = parse_webhook_payload({"event_type": "email_bounced", "lead_email": "jane@acme.com",
                                    "timestamp": "2026-09-24T08:00:00Z"})
    r = handle_reply(bounce, ctx)
    assert r.category == RC.BOUNCE and stage_of(ctx, lead) == Stage.LOST
    assert ctx.store.is_suppressed(email="jane@acme.com")
    unsub = parse_webhook_payload({"event_type": "LEAD_UNSUBSCRIBED", "sl_lead_email": "bob@beta.com"})
    assert classify_rules(unsub, ctx).category == RC.UNSUBSCRIBE
    assert parse_webhook_payload({"event_type": "email_bounced"}) is None


def test_webhook_to_handle_reply_end_to_end(make_ctx, capsys):
    ctx = make_ctx(offer=OFFER, replies={"classifier": "rules"})
    lead = seed_lead(ctx)
    r = handle_reply(parse_webhook_payload(INSTANTLY_REPLY), ctx)
    assert r.category == RC.POSITIVE and r.lead_id == lead.id
    assert r.body == "Sounds good, send me some times."
    assert "Positive reply: Jane Doe (Acme)" in capsys.readouterr().out
