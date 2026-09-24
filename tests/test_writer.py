"""Tests for the copywriters: template writer, guardrails, prompts, AI writer, build_writer."""
from __future__ import annotations

import json
import logging
import re

import pytest

from leadgen.http import HttpError
from leadgen.llm.base import LLMError
from leadgen.llm.openai import LLMConfigError, LLMTruncatedError
from leadgen.models import Company, Contact, Lead, Message, Signal, SignalType
from leadgen.writer import Writer, WriterOutput, build_writer
from leadgen.writer.ai import AIWriter, WriterError, clean_body, clean_subject
from leadgen.writer.guardrails import check_message, check_sequence
from leadgen.writer.prompts import build_feedback_prompt, build_system_prompt, build_user_prompt
from leadgen.writer.template import (TemplateWriter, age_phrase, append_signoff, article, build_values,
                                     clean_title, company_display_name, fill, funding_label, nice_name,
                                     signoff_for, soft_lower, strip_signoff, tidy)
from tests.fakes import FakeLLM

OFFER = {
    "sender_name": "Sam Lee", "sender_title": "Founder", "sender_company": "Northwind Talent",
    "sender_website": "https://northwind.example", "service": "finance recruitment",
    "value_prop": "fill finance roles in under 3 weeks",
    "proof": "Last quarter we placed 14 accountants at firms your size",
    "booking_link": "https://cal.com/sam/15min", "footer": "Not relevant? Reply 'no' and I won't email again.",
}


def make_lead(signals=None, contact=None, name="Acme Widgets Ltd", domain="acme.com", **company_kw):
    if signals is None:
        signals = [Signal(type="job_posting", title="Senior Accountant - Hybrid (m/f/d)",
                          posted_at="2026-09-21", location="Leeds", url="https://jobs.example/1")]
    if contact is None:
        contact = Contact(full_name="jane doe", title="CFO", email="jane@acme.com")
    company = Company(name=name, domain=domain, location="Leeds, UK", signals=signals, contacts=[contact],
                      **company_kw)
    return Lead(company=company, contact=contact, playbook="test")


def body_only(msg, lead, ctx):
    return strip_signoff(msg.body, *signoff_for(lead, ctx))


def ai_response(n=4, subject="senior accountant at acme", first_body=None, **over):
    emails = [{"step": 1, "subject": subject, "body": first_body or (
        "Hi Jane,\n\nSaw Acme is hiring a Senior Accountant this week. That usually means month-end "
        "lands on fewer people for a while.\n\nWe fill finance roles in under 3 weeks.\n\nWorth a quick chat?")}]
    for i in range(2, n + 1):
        emails.append({"step": i, "subject": "", "body": f"Hi Jane,\n\nFollow-up {i}: one more angle on the "
                                                         f"Senior Accountant search at Acme.\n\nOpen to a chat?"})
    data = {"personalization_line": "Saw Acme is hiring a Senior Accountant this week.",
            "pain_hypothesis": "Month-end is landing on fewer people.", "emails": emails}
    data.update(over)
    return data


class RecordingLLM(FakeLLM):
    """FakeLLM that also records max_tokens per call."""

    def __init__(self, *responses):
        super().__init__(*responses)
        self.max_tokens = []

    def complete_json(self, system, user, *, max_tokens=1500, temperature=None):
        self.max_tokens.append(max_tokens)
        return super().complete_json(system, user, max_tokens=max_tokens, temperature=temperature)


@pytest.fixture
def ctx(make_ctx):
    return make_ctx(offer=OFFER)


# =============================================================================================
# Template writer
# =============================================================================================

def test_template_default_sequence(ctx):
    lead = make_lead()
    out = TemplateWriter({}, ctx).write(lead)
    assert isinstance(out, WriterOutput) and out.writer == "template"
    assert out.warnings == []
    assert [m.step for m in out.messages] == [1, 2, 3, 4]
    assert [m.day for m in out.messages] == [1, 3, 7, 12]
    first = out.messages[0]
    assert first.subject == "senior accountant at Acme Widgets"
    assert len(first.subject.split()) <= 6
    assert [m.subject for m in out.messages[1:]] == ["", "", ""]  # follow-ups: same thread
    assert first.body.startswith("Hi Jane,\n\nSaw you're hiring a Senior Accountant this week.")
    assert "We help teams like Acme Widgets fill finance roles in under 3 weeks." in first.body
    assert "Worth a quick chat?" in first.body
    assert "Last quarter we placed 14 accountants" in out.messages[1].body          # bump = proof
    assert "https://cal.com/sam/15min" in out.messages[2].body                      # value = booking
    assert "Good luck with the Senior Accountant hire." in out.messages[3].body     # breakup
    for m in out.messages:
        assert m.body.endswith("Sam Lee\nFounder, Northwind Talent\n\n" + OFFER["footer"])
        assert "{" not in m.body and "  " not in m.body and " ." not in m.body
    assert out.personalization == "Saw you're hiring a Senior Accountant this week."
    assert out.hypothesis == "Usually that means the team is stretched until the seat is filled."


def test_template_first_name_fallback_and_casing(make_ctx):
    ctx = make_ctx()
    for contact, greeting in ((Contact(first_name="", title="CFO"), "Hi there,"),
                              (Contact(first_name="J.", title="CFO"), "Hi there,"),
                              (Contact(first_name="MARY-JANE"), "Hi Mary-Jane,"),
                              (Contact(first_name="jan.kowalski@acme.com"), "Hi there,")):
        out = TemplateWriter({}, ctx).write(make_lead(contact=contact))
        assert out.messages[0].body.startswith(greeting)
    lead = make_lead()
    lead.contact = None
    out = TemplateWriter({}, ctx).write(lead)
    assert out.messages[0].body.startswith("Hi there,")


def test_template_plural_job_postings(ctx):
    sigs = [Signal(type="job_posting", title="Senior Accountant", posted_at="2026-09-20"),
            Signal(type="job_posting", title="AP Clerk", posted_at="2026-09-18"),
            Signal(type="job_posting", title="Payroll Specialist", posted_at="2026-09-10"),
            Signal(type="funding", title="Series A")]
    out = TemplateWriter({}, ctx).write(make_lead(signals=sigs))
    assert out.personalization == "Saw you have 3 open roles, including a Senior Accountant."
    assert "several roles at once" in out.hypothesis
    assert "Quick follow-up on my note about your open roles." in out.messages[1].body
    assert out.warnings == []


def test_template_stale_job_posting(ctx):
    sig = Signal(type="job_posting", title="Accountant", posted_at="2026-09-22", reposted=True)
    out = TemplateWriter({}, ctx).write(make_lead(signals=[sig]))
    assert out.personalization == "Saw you're hiring an Accountant."  # no "this week" for a repost
    assert "stays open that long" in out.hypothesis
    old = Signal(type="job_posting", title="Accountant", posted_at="2026-08-01")
    out2 = TemplateWriter({}, ctx).write(make_lead(signals=[old]))
    assert "stays open that long" in out2.hypothesis


SAMPLE_TITLES = {
    "job_posting": "Operations Manager", "funding": "Globex raises $20M Series B led by Accel",
    "leadership_change": "New COO appointed", "expansion": "Opening new office",
    "headcount_growth": "Headcount +25% in 6 months", "tech_adoption": "Adopted HubSpot",
    "news": "Globex wins regional award", "review": "3.1 stars on G2", "ad_activity": "New Google Ads",
    "website_change": "New pricing page", "event": "SaaStr Annual 2026", "custom": "Opened a warehouse",
}


@pytest.mark.parametrize("stype", SignalType.ALL)
def test_template_every_signal_type_passes_guardrails(make_ctx, stype):
    ctx = make_ctx(offer=OFFER)
    sig = Signal(type=stype, title=SAMPLE_TITLES[stype], posted_at="2026-09-15", location="Austin")
    lead = make_lead(signals=[sig], name="Globex Corporation", domain="globex.io")
    out = TemplateWriter({}, ctx).write(lead)
    assert out.warnings == [], out.warnings
    assert out.personalization and out.personalization[0].isupper() and out.personalization.endswith(".")
    assert out.hypothesis.endswith(".")
    subject = out.messages[0].subject
    assert subject and len(subject.split()) <= 6
    for m in out.messages:
        assert not re.search(r"[{}\[\]<>]", m.body)


def test_template_signal_specific_phrases(ctx):
    def opener(stype, title, **kw):
        lead = make_lead(signals=[Signal(type=stype, title=title, posted_at="2026-09-01", **kw)],
                         name="Globex Inc", domain="globex.io")
        return TemplateWriter({}, ctx).write(lead)

    out = opener("funding", "Globex raises $20 million Series B")
    assert out.personalization == "Congrats on the $20M Series B."
    assert out.messages[0].subject == "Globex's $20M Series B"
    assert opener("funding", "Funding").personalization == "Congrats on the recent funding round."
    assert opener("expansion", "New office", location="Austin").personalization == \
        "Saw Globex is expanding into Austin."
    assert opener("expansion", "New office").personalization == "Saw Globex is expanding."  # dangling "into" removed
    tech = opener("tech_adoption", "Adopted HubSpot")
    assert tech.personalization == "Noticed Globex has started using HubSpot."
    assert tech.messages[0].subject == "HubSpot at Globex"
    assert opener("event", "SaaStr Annual 2026").messages[0].subject == "Globex at SaaStr Annual 2026"
    assert opener("news", "Anything").messages[0].subject == "idea for Globex"
    assert opener("some_new_type", "Whatever").personalization == "Saw the recent update from Globex."


def test_template_without_signals(ctx):
    out = TemplateWriter({}, ctx).write(make_lead(signals=[]))
    assert out.personalization == "Came across Acme Widgets and had a quick idea."
    assert out.messages[0].subject == "idea for Acme Widgets"
    assert out.warnings == []


def test_template_long_title_and_company_keep_subject_short(ctx):
    sig = Signal(type="job_posting", title="Senior Group Financial Reporting and Consolidation Accountant",
                 posted_at="2026-09-20")
    lead = make_lead(signals=[sig], name="Smith & Jones International Accounting Partners LLP")
    subject = TemplateWriter({}, ctx).write(lead).messages[0].subject
    assert 0 < len(subject.split()) <= 6


@pytest.mark.parametrize("days", [[1], [1, 5], [1, 3, 9], [1, 3, 7, 12, 18, 25], [1, 2, 3, 4, 5, 6, 7]])
def test_template_any_sequence_length(make_ctx, days):
    ctx = make_ctx(offer=OFFER, writer={"sequence": [{"day": d, "purpose": "x"} for d in days]})
    out = TemplateWriter({}, ctx).write(make_lead())
    assert [m.day for m in out.messages] == days
    assert out.messages[0].subject and all(m.subject == "" for m in out.messages[1:])
    if len(days) > 1:
        assert "I'll leave it here" in out.messages[-1].body  # breakup always last
    dupes = [w for w in out.warnings if "same body" in w]
    assert out.warnings == dupes  # only a 7-step sequence repeats the last check-in
    assert bool(dupes) == (len(days) > 6)


def test_template_overrides_bodies_and_subjects(make_ctx):
    ctx = make_ctx(offer=OFFER, writer={"templates": {
        "subjects": ["{company} + {signal_title_lc}", "should be kept"],
        "steps": ["Hi {{first_name}}, about {company} {missing_key} and {signal_title}. {cta}",
                  "Hi {first_name}, second note for {company}. {cta}"]}})
    out = TemplateWriter({}, ctx).write(make_lead())
    m = out.messages
    assert m[0].subject == "Acme Widgets + senior accountant"
    assert m[0].body.startswith("Hi Jane, about Acme Widgets and Senior Accountant. Worth a quick chat?")
    assert m[1].subject == "should be kept"  # explicit follow-up subject = new thread
    assert m[2].body == m[1].body and m[3].body == m[1].body  # reuse the last follow-up style
    assert m[2].subject == "" and m[3].subject == ""
    assert any("same body as step 2" in w for w in out.warnings)


def test_template_override_dict_steps_and_single_step(make_ctx):
    ctx = make_ctx(offer=OFFER, writer={"templates": {"steps": [
        {"subject": "quick q, {first_name}", "body": "Hi {first_name}, custom opener for {company}."}]}})
    out = TemplateWriter({}, ctx).write(make_lead())
    assert out.messages[0].subject == "quick q, Jane"
    assert out.messages[0].body.startswith("Hi Jane, custom opener for Acme Widgets.")
    assert out.messages[1].body.startswith("Hi Jane,\n\nQuick follow-up")  # defaults for the rest
    ctx2 = make_ctx(offer=OFFER, writer={"templates": {"subject": "hello {company}"}})
    out2 = TemplateWriter({}, ctx2).write(make_lead())
    assert out2.messages[0].subject == "hello Acme Widgets"
    assert out2.messages[0].body.startswith("Hi Jane,\n\nSaw you're hiring")


def test_template_malformed_overrides_are_ignored(make_ctx, caplog):
    ctx = make_ctx(offer=OFFER, writer={"templates": {"steps": [{"subject": "no body"}, 42]}})
    with caplog.at_level(logging.WARNING, logger="leadgen.test"):
        out = TemplateWriter({}, ctx).write(make_lead())
    assert len(out.messages) == 4 and out.messages[0].body.startswith("Hi Jane,\n\nSaw you're hiring")
    assert "needs a body" in caplog.text
    ctx2 = make_ctx(offer=OFFER, writer={"templates": {"steps": "Hi {first_name}, only one."}})
    assert TemplateWriter({}, ctx2).write(make_lead()).messages[0].body.startswith("Hi Jane, only one.")


def test_template_phrase_overrides(make_ctx):
    ctx = make_ctx(offer=OFFER, writer={"templates": {
        "signal_phrases": {"job_posting": "noticed the {signal_title} opening at {company}"},
        "hypotheses": {"default": "Open seats slow {company} down."},
        "topics": {"job_posting": "the {signal_title} search"}}})
    out = TemplateWriter({}, ctx).write(make_lead())
    assert out.personalization.startswith("Noticed the Senior Accountant opening at Acme Widgets")
    assert out.hypothesis == "Open seats slow Acme Widgets down."
    assert "the Senior Accountant search" in out.messages[1].body
    sig = Signal(type="custom", title="x", data={"phrase": "loved your post about {company}'s roadmap",
                                                 "hypothesis": "Roadmaps need hands.", "topic": "the roadmap"})
    out2 = TemplateWriter({}, make_ctx(offer=OFFER)).write(make_lead(signals=[sig]))
    assert out2.personalization == "Loved your post about Acme Widgets's roadmap."
    assert out2.hypothesis == "Roadmaps need hands." and "the roadmap" in out2.messages[1].body


def test_template_offer_variants(make_ctx):
    bare = make_ctx()  # no offer at all: still reads fine, no signature
    out = TemplateWriter({}, bare).write(make_lead())
    first = out.messages[0].body
    assert first == ("Hi Jane,\n\nSaw you're hiring a Senior Accountant this week.\n\n"
                     "Usually that means the team is stretched until the seat is filled.\n\nWorth a quick chat?")
    assert out.warnings == []
    assert "Happy to share what's worked" in out.messages[1].body
    assert "grab a time" not in out.messages[2].body

    svc = make_ctx(offer={"service": "outsourced bookkeeping", "sender_company": "Ledger Co",
                          "sender_name": "Ana", "cta": "open to a quick call about {company}?"})
    v = build_values(make_lead(), svc)
    assert v["value_line"] == "At Ledger Co, we focus on outsourced bookkeeping."
    assert v["cta"] == "Open to a quick call about Acme Widgets?"
    assert v["signature"] == "Ana\nLedger Co"
    sentence_vp = make_ctx(offer={"value_prop": "Our clients close their books 5 days faster"})
    assert build_values(make_lead(), sentence_vp)["value_line"] == "Our clients close their books 5 days faster."
    cap_vp = make_ctx(offer={"value_prop": "Hire vetted accountants in 10 days"})
    assert build_values(make_lead(), cap_vp)["value_line"] == \
        "We help teams like Acme Widgets hire vetted accountants in 10 days."


def test_template_custom_signature_and_footer(make_ctx):
    ctx = make_ctx(offer=dict(OFFER, signature="Cheers,\n{sender_name} | {sender_website}",
                              footer="{sender_company}, 1 Main St. Reply stop to opt out."))
    out = TemplateWriter({}, ctx).write(make_lead())
    for m in out.messages:
        assert m.body.endswith("Cheers,\nSam Lee | https://northwind.example\n\n"
                               "Northwind Talent, 1 Main St. Reply stop to opt out.")
    assert out.warnings == []


def test_template_reports_custom_template_problems(make_ctx):
    ctx = make_ctx(offer=OFFER, writer={"max_words": 20, "templates": {"steps": [
        "Hi {first_name}, I wanted to reach out about {company}. " + "More words here. " * 10]}})
    out = TemplateWriter({}, ctx).write(make_lead())
    assert any(w.startswith("template: step 1: uses banned phrase") for w in out.warnings)
    assert any("step 1: body is" in w and "(max 20)" in w for w in out.warnings)


def test_template_writer_is_offline_and_registered(ctx):
    from leadgen import registry
    assert registry.resolve("writer", "template") is TemplateWriter
    assert TemplateWriter.offline is True and TemplateWriter.name == "template"
    assert issubclass(TemplateWriter, Writer)


# --- text helpers ------------------------------------------------------------------------------

def test_fill_is_safe():
    vals = {"a": "1", "name": "Jane"}
    assert fill("{a} {{name}} { name } {missing}", vals) == "1 Jane Jane "
    assert fill("braces {0} {a!r} {} {{ }} 50% {", vals) == "braces {0} {a!r} {} {{ }} 50% {"
    assert fill(None, vals) == ""


@pytest.mark.parametrize("raw, expected", [
    ("saw you have 3 open roles, including a .", "saw you have 3 open roles."),
    ("saw you're hiring a .", "saw you're hiring."),
    ("Saw Acme is expanding into .", "Saw Acme is expanding."),
    ("Hi  there ,  how are you ?", "Hi there, how are you?"),
    ("a\n\n\n\n\nb", "a\n\nb"),
    ("text () here", "text here"),
    ("line one.\n.\n, leftover", "line one.\n\nleftover"),
    ("ok , .", "ok."),
    ("wait...", "wait..."),
    ("ends with..", "ends with."),
    ("What are you looking at?", "What are you looking at?"),
    ("  spaced   out  \t text ", "spaced out text"),
    ("Accountant at ", "Accountant"),
])
def test_tidy(raw, expected):
    assert tidy(raw) == expected


def test_title_and_name_helpers():
    assert clean_title("Senior Accountant - Hybrid (m/f/d) | £45k") == "Senior Accountant"
    assert clean_title("Head of Sales, EMEA") == "Head of Sales"
    assert clean_title("SENIOR ACCOUNTANT") == "Senior Accountant"
    assert clean_title("HR MANAGER") == "HR Manager"
    assert clean_title("Accountant [Remote]") == "Accountant"
    assert clean_title("") == ""
    assert soft_lower("Senior HR Manager") == "senior HR manager"
    assert soft_lower("DevOps Engineer (iOS)") == "DevOps engineer (iOS)"
    assert [article(x) for x in ("HR Manager", "UX Designer", "Accountant", "University Liaison",
                                 "SEO Lead", "Hourly Worker", "Engineer", "", "Manager")] == \
        ["an", "a", "an", "a", "an", "an", "an", "a", "a"]
    assert company_display_name("Acme Widgets Ltd.") == "Acme Widgets"
    assert company_display_name("Globex Pty Ltd") == "Globex"
    assert company_display_name("Initech, Inc.") == "Initech"
    assert company_display_name("Ltd") == "Ltd"
    assert nice_name("JANE") == "Jane" and nice_name("jean-luc") == "Jean-Luc" and nice_name("McKay") == "McKay"
    assert nice_name("x") == "" and nice_name("user123") == ""


def test_funding_and_age_helpers():
    assert funding_label("Acme raises $20M Series B led by Accel") == "$20M Series B"
    assert funding_label("Raised $20 million seed") == "$20M seed round"
    assert funding_label("£1.5bn IPO") == "£1.5B IPO"
    assert funding_label("Pre-seed round") == "pre-seed round"
    assert funding_label("Closed €500k") == "€500K raise"
    assert funding_label("Big news") == ""
    assert [age_phrase(a) for a in (None, 0, 1, 3, 10, 45, 200)] == \
        ["recently", "today", "yesterday", "this week", "recently", "a few weeks ago", "a while back"]


def test_signoff_helpers():
    body = append_signoff("Hi Jane,\n\nText.\n", "Sam\nFounder", "Footer line")
    assert body == "Hi Jane,\n\nText.\n\nSam\nFounder\n\nFooter line"
    assert strip_signoff(body, "Sam\nFounder", "Footer line") == "Hi Jane,\n\nText."
    assert strip_signoff("Hi Jane", "", "") == "Hi Jane"
    assert append_signoff("Body", "", "") == "Body"


# =============================================================================================
# Guardrails
# =============================================================================================

def msg(body, subject="", step=1, lead=None, ctx=None, signoff=True):
    if signoff and lead is not None and ctx is not None:
        body = append_signoff(body, *signoff_for(lead, ctx))
    return Message(step=step, day=1, subject=subject, body=body)


GOOD_BODY = ("Hi Jane,\n\nSaw Acme is hiring a Senior Accountant this week. That usually means month-end "
             "lands on fewer people.\n\nWorth a quick chat?")


def test_guardrails_clean_message_passes(ctx):
    lead = make_lead()
    assert check_message(msg(GOOD_BODY, "accountant at acme", lead=lead, ctx=ctx), lead, ctx, 0) == []


def test_guardrails_word_count_excludes_signature(make_ctx):
    ctx = make_ctx(offer=dict(OFFER, signature="Sam Lee\n" + "long signature words " * 30), writer={"max_words": 30})
    lead = make_lead()
    ok = msg(GOOD_BODY, "accountant at acme", lead=lead, ctx=ctx)
    assert check_message(ok, lead, ctx, 0) == []
    long_body = GOOD_BODY + " extra" * 20
    problems = check_message(msg(long_body, "accountant at acme", lead=lead, ctx=ctx), lead, ctx, 0)
    assert problems == ["step 1: body is 43 words (max 30)"]  # 23 + 20


def test_guardrails_banned_phrases_case_insensitive(ctx):
    lead = make_lead()
    body = GOOD_BODY + "\n\nJust wanted to TOUCH BASE. We're a world-class team."
    problems = check_message(msg(body, "x at acme", lead=lead, ctx=ctx), lead, ctx, 0)
    assert 'step 1: uses banned phrase "touch base"' in problems
    assert 'step 1: uses banned phrase "world-class"' in problems
    subj = check_message(msg(GOOD_BODY, "Synergy at Acme", lead=lead, ctx=ctx), lead, ctx, 0)
    assert subj == ['step 1: uses banned phrase "synergy"']


@pytest.mark.parametrize("token", ["{first_name}", "{{company}}", "[Name]", "[Your Company]", "<first_name>",
                                   "{ company name }", "[first_name]", "<b>"])
def test_guardrails_leftover_placeholders(ctx, token):
    lead = make_lead()
    problems = check_message(msg(GOOD_BODY + f" See {token}.", "x at acme", lead=lead, ctx=ctx), lead, ctx, 0)
    assert any(p.startswith("step 1: leftover placeholder") and token in p for p in problems), problems


def test_guardrails_empty_body(ctx):
    lead = make_lead()
    problems = check_message(msg("", "x at acme", lead=lead, ctx=ctx), lead, ctx, 0)
    assert problems == ["step 1: empty body"]
    assert check_message(Message(step=2, day=3, subject="", body="   "), lead, ctx, 1) == ["step 2: empty body"]


def test_guardrails_step_one_subject(ctx):
    lead = make_lead()
    assert check_message(msg(GOOD_BODY, "", lead=lead, ctx=ctx), lead, ctx, 0) == ["step 1: subject is empty"]
    nine = "one two three four five six seven eight nine"
    assert check_message(msg(GOOD_BODY, nine, lead=lead, ctx=ctx), lead, ctx, 0) == \
        ["step 1: subject is 9 words (max 8)"]
    long_subj = "supercalifragilistic " * 4
    assert check_message(msg(GOOD_BODY, long_subj.strip(), lead=lead, ctx=ctx), lead, ctx, 0) == \
        ["step 1: subject is 83 characters (max 70)"]
    # follow-ups: empty subject is the thread convention, not a problem
    assert check_message(msg(GOOD_BODY, "", step=2, lead=lead, ctx=ctx), lead, ctx, 1) == []


def test_guardrails_step_one_must_mention_company_or_signal(ctx):
    lead = make_lead()
    vague = "Hi Jane,\n\nNoticed some things lately. We help teams.\n\nWorth a quick chat?"
    problems = check_message(msg(vague, "quick question", lead=lead, ctx=ctx), lead, ctx, 0)
    assert problems == ['step 1: does not mention "Acme Widgets Ltd" or the signal "Senior Accountant"']
    title_only = "Hi Jane,\n\nSaw the senior accountant opening.\n\nWorth a quick chat?"
    assert check_message(msg(title_only, "quick question", lead=lead, ctx=ctx), lead, ctx, 0) == []
    in_subject = check_message(msg(vague, "idea for Acme Widgets", lead=lead, ctx=ctx), lead, ctx, 0)
    assert in_subject == []
    domain_lead = make_lead(name="", domain="initech.com", signals=[])
    body = "Hi Jane,\n\nSaw Initech is growing.\n\nWorth a quick chat?"
    assert check_message(msg(body, "idea", lead=domain_lead, ctx=ctx), domain_lead, ctx, 0) == []
    # follow-ups do not need the mention
    assert check_message(msg(vague, "", step=2, lead=lead, ctx=ctx), lead, ctx, 1) == []


def test_guardrails_exclamation_marks(ctx):
    lead = make_lead()
    assert check_message(msg(GOOD_BODY + " Great!", "acme", lead=lead, ctx=ctx), lead, ctx, 0) == []
    problems = check_message(msg(GOOD_BODY + " Great! Really!", "acme!", lead=lead, ctx=ctx), lead, ctx, 0)
    assert problems == ["step 1: 3 exclamation marks (max 1)"]


def test_guardrails_links(ctx):
    lead = make_lead()
    ok = GOOD_BODY + " Book here: https://cal.com/sam/15min. More at https://northwind.example/case-study"
    assert check_message(msg(ok, "acme", lead=lead, ctx=ctx), lead, ctx, 0) == []
    bad = GOOD_BODY + " See https://cal.com/someone-else and www.bit.ly/xyz."
    problems = check_message(msg(bad, "acme", lead=lead, ctx=ctx), lead, ctx, 0)
    assert problems == [
        "step 1: link not allowed: https://cal.com/someone-else (only offer.booking_link / offer.sender_website)",
        "step 1: link not allowed: www.bit.ly/xyz (only offer.booking_link / offer.sender_website)"]
    # links inside the appended footer are not the model's doing
    ctx2_footer = dict(OFFER, footer="Unsubscribe: https://optout.example/u")
    from tests.conftest import build_ctx
    ctx2 = build_ctx(offer=ctx2_footer)
    try:
        assert check_message(msg(GOOD_BODY, "acme", lead=lead, ctx=ctx2), lead, ctx2, 0) == []
    finally:
        ctx2.store.close()


@pytest.mark.parametrize("text, label", [
    ("It's FREE! for you", "free!"), ("Results guaranteed.", "guarantee"), ("A risk free trial", "risk-free"),
    ("Act now please", "act now"), ("100 % happy", "100%"), ("Make $$$ fast", "$$$"),
    ("Click here to see", "click here"),
])
def test_guardrails_spammy_words(ctx, text, label):
    lead = make_lead()
    problems = check_message(msg(GOOD_BODY + " " + text, "acme", lead=lead, ctx=ctx), lead, ctx, 0)
    assert f'step 1: spammy wording "{label}"' in problems


def test_guardrails_all_caps(make_ctx):
    ctx = make_ctx(offer=OFFER, writer={"acronyms": ["ACMEX"]})
    lead = make_lead(name="NVIDIA Corporation")
    body = GOOD_BODY + " This is URGENT. HIPAA and GDPR are fine, so is NVIDIA and ACMEX. OK?"
    problems = check_message(msg(body, "acme", lead=lead, ctx=ctx), lead, ctx, 0)
    assert problems == ['step 1: ALL-CAPS word "URGENT"']


def test_check_sequence_count_and_duplicates(ctx):
    lead = make_lead()
    m1 = msg(GOOD_BODY, "acme", lead=lead, ctx=ctx)
    m2 = msg("Hi Jane, bump.", "", step=2, lead=lead, ctx=ctx)
    m3 = msg("Hi Jane,  bump!", "", step=3, lead=lead, ctx=ctx)
    problems = check_sequence([m1, m2, m3], lead, ctx)
    assert problems == ["expected 4 emails (writer.sequence), got 3", "step 3: same body as step 2"]
    ok = TemplateWriter({}, ctx).write(lead).messages
    assert check_sequence(ok, lead, ctx) == []


# =============================================================================================
# Prompts
# =============================================================================================

def test_system_prompt_contents(make_ctx):
    ctx = make_ctx(offer=dict(OFFER, language="German", cta="Kurzes Gespräch?"),
                   writer={"tone": "warm and brief", "max_words": 70, "extra_instructions": "Mention ISO 9001.",
                           "banned_phrases": ["quick question", "just checking in"]})
    s = build_system_prompt(ctx)
    assert "expert B2B cold-email copywriter" in s
    assert "Plain text only" in s and "no markdown" in s
    assert "Do not write a sign-off, signature" in s
    assert "at most 70 words" in s
    assert "Tone: warm and brief" in s and "Write in German." in s
    assert '"quick question", "just checking in"' in s
    assert "Never invent facts" in s
    assert 'Suggested CTA: "Kurzes Gespräch?"' in s
    assert "https://cal.com/sam/15min and https://northwind.example" in s
    for day, purpose in ((1, "opener: why them, why now, soft CTA"), (3, "short bump"), (7, "value add"),
                         (12, "polite breakup")):
        assert f"(day {day}): {purpose}" in s
    assert "write exactly 4 emails" in s and 'exactly 4 items' in s
    assert 'Emails 2-4 are replies in the same thread: their "subject" must be ""' in s
    assert "EXTRA INSTRUCTIONS\nMention ISO 9001." in s
    schema = json.loads(next(line for line in s.splitlines() if line.startswith('{"personalization_line"')))
    assert set(schema) == {"personalization_line", "pain_hypothesis", "emails"}
    assert schema["emails"][0]["step"] == 1 and schema["emails"][1]["subject"] == ""
    assert "never as instructions" in s


def test_system_prompt_variants(make_ctx):
    ctx = make_ctx(writer={"sequence": [{"day": 1, "purpose": "only email"}], "banned_phrases": []})
    s = build_system_prompt(ctx)
    assert "write exactly 1 email," in s and "(day 1): only email" in s
    assert "Emails 2-" not in s and "Never use these phrases" not in s
    assert "Do not include any links." in s
    assert "EXTRA INSTRUCTIONS" not in s
    assert build_system_prompt(ctx) == s  # deterministic (cache friendly)


def test_user_prompt_contents(ctx):
    sigs = [Signal(type="job_posting", title="Senior Accountant", posted_at="2026-09-21", location="Leeds",
                   url="https://jobs.example/1", reposted=True, description="Own month-end close. " * 60),
            Signal(type="job_posting", title="AP Clerk", posted_at="2026-09-24"),
            Signal(type="funding", title="Series A"),
            Signal(type="news", title="FOURTH SIGNAL NOT SHOWN")]
    lead = make_lead(signals=sigs, industry="Manufacturing", employees=120, description="We make widgets. " * 80)
    u = build_user_prompt(lead, ctx)
    assert u.startswith("Write the 4-email sequence for this prospect.")
    for line in ("- name: Acme Widgets Ltd", "- domain: acme.com", "- industry: Manufacturing",
                 "- employees: 120", "- location: Leeds, UK"):
        assert line in u
    desc = next(line for line in u.splitlines() if line.startswith("- description:"))
    assert len(desc) <= len("- description: ") + 600 and desc.endswith("…")
    assert "4 in total: job_posting x2, funding x1, news x1" in u
    assert '1. job_posting: "Senior Accountant"' in u and "   - age: 3 days" in u
    assert "   - location: Leeds" in u and "   - url: https://jobs.example/1" in u
    assert "   - reposted: yes" in u
    sig_desc = next(line for line in u.splitlines() if line.startswith("   - description:"))
    assert len(sig_desc) <= len("   - description: ") + 400
    assert '2. job_posting: "AP Clerk"' in u and "   - age: today" in u
    assert "   - age: unknown" in u and "   - reposted: no" in u
    assert "FOURTH SIGNAL NOT SHOWN" not in u
    assert "- first name: Jane" in u and "- title: CFO" in u
    for line in ("- sender name: Sam Lee", "- sender company: Northwind Talent", "- service: finance recruitment",
                 "- value proposition: fill finance roles in under 3 weeks", "- proof: Last quarter",
                 "- call to action: Worth a quick chat?", "- booking link: https://cal.com/sam/15min"):
        assert line in u
    assert "jane@acme.com" not in u  # the contact's email is not needed by the model


def test_user_prompt_sparse_lead(make_ctx):
    ctx = make_ctx(offer={"cta": ""})
    lead = make_lead(signals=[], contact=Contact(), domain="", name="Bare Co")
    u = build_user_prompt(lead, ctx)
    assert "- domain:" not in u and "- industry:" not in u and "- employees:" not in u
    assert "(none recorded - do not invent one" in u
    assert '(first name unknown - greet with "Hi there,")' in u
    assert "(no details given" in u


def test_feedback_prompt():
    fb = build_feedback_prompt("USER PROMPT", ["step 1: uses banned phrase \"synergy\"", "step 2: empty body"],
                               {"emails": [{"step": 1, "body": "synergy"}]})
    assert fb.startswith("USER PROMPT\n\nYOUR PREVIOUS ATTEMPT BROKE THESE RULES:")
    assert '- step 1: uses banned phrase "synergy"' in fb and "- step 2: empty body" in fb
    assert '"body": "synergy"' in fb and fb.rstrip().endswith("return the full JSON object again.")
    assert "Previous attempt" not in build_feedback_prompt("U", ["x"])


# =============================================================================================
# AI writer
# =============================================================================================

def ai_ctx(make_ctx, *responses, **overrides):
    ctx = make_ctx(env={"OPENAI_API_KEY": "sk-test"}, offer=OFFER,
                   writer=dict({"type": "ai", "provider": "openai"}, **overrides))
    ctx.llm = RecordingLLM(*responses)
    return ctx


def test_ai_writer_happy_path(make_ctx):
    data = ai_response()
    data["emails"][0]["body"] += "\n\nBest,\nSam"            # model signs off anyway -> stripped
    data["emails"][1]["subject"] = "Re: senior accountant"  # follow-up subject -> forced ""
    ctx = ai_ctx(make_ctx, data)
    lead = make_lead()
    out = AIWriter({}, ctx).write(lead)
    assert out.writer == "ai:fake-model" and out.warnings == []
    assert [m.day for m in out.messages] == [1, 3, 7, 12]
    assert out.messages[0].subject == "senior accountant at acme"
    assert [m.subject for m in out.messages[1:]] == ["", "", ""]
    sig = "Sam Lee\nFounder, Northwind Talent\n\n" + OFFER["footer"]
    for m in out.messages:
        assert m.body.endswith(sig)
    assert "Best," not in out.messages[0].body
    assert out.personalization == "Saw Acme is hiring a Senior Accountant this week."
    assert out.hypothesis == "Month-end is landing on fewer people."
    call = ctx.llm.calls[0]
    assert call["json_mode"] is True
    assert call["system"] == build_system_prompt(ctx) and call["user"] == build_user_prompt(lead, ctx)
    assert ctx.llm.max_tokens == [4000]


def test_ai_writer_maps_steps_by_number_and_cleans_output(make_ctx):
    data = ai_response()
    data["emails"] = list(reversed(data["emails"]))
    data["emails"][-1]["body"] = "**Hi Jane,**\\n\\nSaw Acme is hiring a Senior Accountant.\\n\\nWorth a chat?"
    data["emails"][-1]["subject"] = 'Subject: "accountant at acme"'
    del data["personalization_line"]
    ctx = ai_ctx(make_ctx, data)
    out = AIWriter({}, ctx).write(make_lead())
    assert out.warnings == []
    assert out.messages[0].body.startswith("Hi Jane,\n\nSaw Acme is hiring a Senior Accountant.\n\nWorth a chat?")
    assert out.messages[0].subject == "accountant at acme"
    assert out.messages[1].body.startswith("Hi Jane,\n\nFollow-up 2:")
    assert out.personalization == "Saw Acme is hiring a Senior Accountant."


def test_ai_writer_retries_once_with_feedback(make_ctx):
    bad = ai_response()
    bad["emails"][1]["body"] = "Hi Jane, just wanted to touch base. Great! Really!"
    ctx = ai_ctx(make_ctx, bad, ai_response())
    out = AIWriter({}, ctx).write(make_lead())
    assert out.writer == "ai:fake-model" and out.warnings == []
    assert len(ctx.llm.calls) == 2
    retry = ctx.llm.calls[1]["user"]
    assert retry.startswith(ctx.llm.calls[0]["user"])
    assert 'step 2: uses banned phrase "touch base"' in retry
    assert "step 2: 2 exclamation marks (max 1)" in retry
    assert "Previous attempt" in retry


def test_ai_writer_falls_back_after_second_failure(make_ctx):
    bad = ai_response(subject="")
    ctx = ai_ctx(make_ctx, bad, bad)
    out = AIWriter({}, ctx).write(make_lead())
    assert len(ctx.llm.calls) == 2
    assert out.writer == "template"
    assert out.warnings[0].startswith("ai fallback: copy failed guardrails after retry: step 1: subject is empty")
    assert out.messages[0].subject == "senior accountant at Acme Widgets"


def test_ai_writer_raises_when_fallback_disabled(make_ctx):
    bad = ai_response(subject="")
    ctx = ai_ctx(make_ctx, bad, bad, fallback_to_template=False)
    with pytest.raises(WriterError, match="subject is empty"):
        AIWriter({}, ctx).write(make_lead())
    ctx2 = ai_ctx(make_ctx, LLMError("empty model output"), fallback_to_template=False)
    with pytest.raises(WriterError, match="empty model output") as ei:
        AIWriter({}, ctx2).write(make_lead())
    assert isinstance(ei.value.__cause__, LLMError)


@pytest.mark.parametrize("response, reason", [
    ("this is not json at all", "did not return a JSON object"),
    ({"emails": "nope"}, 'response has no "emails" list'),
    ({"something": "else"}, 'response has no "emails" list'),
])
def test_ai_writer_bad_shapes(make_ctx, response, reason):
    ctx = ai_ctx(make_ctx, response, response)
    out = AIWriter({}, ctx).write(make_lead())
    assert out.writer == "template" and reason in out.warnings[0]


def test_ai_writer_wrong_email_count_retries_with_all_problems(make_ctx):
    short = ai_response(n=3)
    short["emails"][1]["body"] = "Hi Jane, act now!"
    ctx = ai_ctx(make_ctx, short, ai_response())
    out = AIWriter({}, ctx).write(make_lead())
    assert out.writer == "ai:fake-model"
    retry = ctx.llm.calls[1]["user"]
    assert "response has 3 emails, expected exactly 4" in retry and "step 4: missing" in retry
    assert 'step 2: spammy wording "act now"' in retry


def test_ai_writer_llm_errors_fall_back(make_ctx):
    ctx = ai_ctx(make_ctx, LLMError("model refused"),
                 HttpError(503, "https://api.openai.com/v1", "overloaded " * 100), ai_response())
    w = AIWriter({}, ctx)
    first = w.write(make_lead())
    assert first.writer == "template" and first.warnings[0] == "ai fallback: LLMError: model refused"
    second = w.write(make_lead())
    assert second.warnings[0].startswith("ai fallback: HttpError: HTTP 503")
    assert len(second.warnings[0]) <= len("ai fallback: ") + 300
    third = w.write(make_lead())  # transient errors do not disable the writer
    assert third.writer == "ai:fake-model"


def test_ai_writer_disables_after_permanent_errors(make_ctx, caplog):
    ctx = ai_ctx(make_ctx, HttpError(401, "https://api.openai.com/v1", "Incorrect API key"), ai_response())
    w = AIWriter({}, ctx)
    with caplog.at_level(logging.WARNING, logger="leadgen.test"):
        w.write(make_lead())
        out = w.write(make_lead())
    assert len(ctx.llm.calls) == 1  # second lead: no call
    assert out.warnings[0].startswith("ai fallback: LLM request rejected (HTTP 401)")
    assert caplog.text.count("ai writer disabled") == 1

    ctx2 = ai_ctx(make_ctx, LLMConfigError("openai_compatible: base_url and model required"), ai_response())
    w2 = AIWriter({}, ctx2)
    w2.write(make_lead())
    assert w2.write(make_lead()).warnings[0] == "ai fallback: openai_compatible: base_url and model required"
    assert len(ctx2.llm.calls) == 1


def test_ai_writer_disables_after_repeated_transient_failures(make_ctx):
    errs = [LLMError("model refused"), HttpError(500, "u", "boom")]
    ctx = ai_ctx(make_ctx, *errs, ai_response(), max_llm_failures=2)
    w = AIWriter({}, ctx)
    w.write(make_lead())
    w.write(make_lead())
    out = w.write(make_lead())
    assert len(ctx.llm.calls) == 2
    assert "2 consecutive LLM failures (last: HttpError: HTTP 500" in out.warnings[0]


def test_ai_writer_missing_credential_with_real_client(make_ctx):
    ctx = make_ctx(env={}, offer=OFFER, writer={"type": "ai", "provider": "anthropic"})
    w = build_writer(ctx)
    assert isinstance(w, AIWriter)  # key is resolved lazily, at the first call
    out = w.write(make_lead())
    assert out.writer == "template"
    assert "ANTHROPIC_API_KEY" in out.warnings[0] and out.warnings[0].startswith("ai fallback:")
    assert w.write(make_lead()).writer == "template"
    assert ctx.http.calls == []


def test_ai_writer_without_llm_or_in_dry_run(make_ctx):
    ctx = make_ctx(offer=OFFER)
    ctx.llm = None
    out = AIWriter({}, ctx).write(make_lead())
    assert out.writer == "template" and out.warnings[0].startswith("ai fallback: no LLM available")
    dry = ai_ctx(make_ctx, ai_response())
    dry.dry_run = True
    out2 = AIWriter({}, dry).write(make_lead())
    assert out2.warnings[0] == "ai fallback: dry-run (no LLM calls)" and dry.llm.calls == []


def test_ai_writer_retries_truncated_output_with_bigger_budget(make_ctx):
    ctx = ai_ctx(make_ctx, LLMTruncatedError("output truncated"), ai_response(), max_tokens=3000)
    out = AIWriter({}, ctx).write(make_lead())
    assert out.writer == "ai:fake-model"
    assert ctx.llm.max_tokens == [3000, 6000]
    ctx2 = ai_ctx(make_ctx, LLMTruncatedError("output truncated"), LLMTruncatedError("output truncated"))
    out2 = AIWriter({}, ctx2).write(make_lead())
    assert out2.writer == "template" and "output truncated" in out2.warnings[0]


def test_ai_writer_custom_sequence_length(make_ctx):
    seq = [{"day": 1, "purpose": "opener"}, {"day": 4, "purpose": "breakup"}]
    ctx = ai_ctx(make_ctx, ai_response(n=2), sequence=seq)
    out = AIWriter({}, ctx).write(make_lead())
    assert [(m.step, m.day) for m in out.messages] == [(1, 1), (2, 4)]
    assert "write exactly 2 emails" in ctx.llm.calls[0]["system"]


def test_clean_helpers():
    assert clean_subject('  Subject: "quick idea"  ') == "quick idea"
    assert clean_subject(None) == ""
    assert clean_body("```\nHi Jane,\n\nText.\n```") == "Hi Jane,\n\nText."
    assert clean_body("Hi Jane,\n\nText.\n\nThanks,\n[Your Name]", "Sam") == "Hi Jane,\n\nText."
    assert clean_body("Hi Jane,\n\nThanks!\n\nOne idea for you here.\n\nWorth a chat?", "Sam") == \
        "Hi Jane,\n\nThanks!\n\nOne idea for you here.\n\nWorth a chat?"
    assert clean_body("Hi,\n\nText.\n\nBest,\nSam Lee\nFounder\nNorthwind", "Sam Lee") == "Hi,\n\nText."
    assert clean_body(None) == ""


# --- end-to-end through the real HTTP clients ------------------------------------------------

def test_ai_writer_end_to_end_openai(make_ctx):
    ctx = make_ctx(env={"OPENAI_API_KEY": "sk-test"}, offer=OFFER,
                   writer={"type": "ai", "provider": "openai", "model": "gpt-5-mini"})
    payload = {"id": "chatcmpl-1", "object": "chat.completion", "model": "gpt-5-mini",
               "choices": [{"index": 0, "finish_reason": "stop",
                            "message": {"role": "assistant", "content": json.dumps(ai_response()),
                                        "refusal": None}}],
               "usage": {"prompt_tokens": 900, "completion_tokens": 500, "total_tokens": 1400}}
    ctx.http.add("POST", "https://api.openai.com/v1/chat/completions", json=payload)
    writer = build_writer(ctx)
    out = writer.write(make_lead())
    assert out.writer == "ai:gpt-5-mini" and out.warnings == [] and len(out.messages) == 4
    body = ctx.http.calls[0]["json"]
    assert body["response_format"] == {"type": "json_object"} and body["max_completion_tokens"] == 4000
    assert body["messages"][0]["content"].startswith("You are an expert B2B cold-email copywriter")
    assert "PROSPECT COMPANY" in body["messages"][1]["content"]


def test_ai_writer_end_to_end_anthropic(make_ctx):
    ctx = make_ctx(env={"ANTHROPIC_API_KEY": "sk-ant"}, offer=OFFER,
                   writer={"type": "ai", "provider": "anthropic"})
    text = "```json\n" + json.dumps(ai_response()) + "\n```"
    payload = {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5",
               "content": [{"type": "thinking", "thinking": "", "signature": "sig"},
                           {"type": "text", "text": text}],
               "stop_reason": "end_turn", "stop_sequence": None,
               "usage": {"input_tokens": 900, "output_tokens": 700}}
    ctx.http.add("POST", "https://api.anthropic.com/v1/messages", json=payload)
    out = build_writer(ctx).write(make_lead())
    assert out.writer == "ai:claude-sonnet-5" and out.warnings == []
    body = ctx.http.calls[0]["json"]
    assert body["max_tokens"] == 4000 and len(body["messages"]) == 1
    assert "single JSON object and nothing else" in body["system"]


# =============================================================================================
# build_writer
# =============================================================================================

def test_build_writer_selection(make_ctx, caplog):
    assert isinstance(build_writer(make_ctx()), TemplateWriter)

    ai = make_ctx(env={"OPENAI_API_KEY": "k"}, writer={"type": "ai", "provider": "openai"})
    assert isinstance(build_writer(ai), AIWriter)

    dry = make_ctx(env={"OPENAI_API_KEY": "k"}, dry_run=True, writer={"type": "ai", "provider": "openai"})
    with caplog.at_level(logging.INFO, logger="leadgen.test"):
        assert isinstance(build_writer(dry), TemplateWriter)
    assert "dry-run" in caplog.text
    assert dry._llm_loaded is False  # the LLM is never even built in a dry run

    no_llm = make_ctx(writer={"type": "ai", "provider": "openai"})
    no_llm.llm = None
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="leadgen.test"):
        assert isinstance(build_writer(no_llm), TemplateWriter)
    assert "no LLM is available" in caplog.text


def test_pipeline_uses_build_writer():
    import leadgen.pipeline as pipeline
    assert pipeline.build_writer is build_writer


# =============================================================================================
# Regressions
# =============================================================================================

def test_ai_writer_network_error_reason_never_carries_the_request_headers(make_ctx):
    # requests' InvalidHeader quotes the header value (= the API key); HttpClient wraps it as HTTP 0
    leak = HttpError(0, "https://api.openai.com/v1/chat/completions",
                     "InvalidHeader: Invalid leading whitespace, reserved character(s), or return "
                     "character(s) in header value: 'Bearer sk-live-SECRETSECRET\\n'")
    ctx = ai_ctx(make_ctx, leak, leak, leak, leak, max_llm_failures=3)
    w = AIWriter({}, ctx)
    outs = [w.write(make_lead()) for _ in range(4)]
    for out in outs:
        assert out.writer == "template" and "SECRET" not in " ".join(out.warnings)
    assert outs[0].warnings[0] == "ai fallback: HttpError: LLM request failed without a response (InvalidHeader)"
    assert w._disabled and "SECRET" not in w._disabled  # the disabled reason reaches every later lead
    # an ordinary HTTP error keeps its useful detail
    ctx2 = ai_ctx(make_ctx, HttpError(500, "https://api.openai.com/v1/chat/completions", "overloaded"))
    assert "HTTP 500" in AIWriter({}, ctx2).write(make_lead()).warnings[0]


@pytest.mark.parametrize("raw, expected", [
    ("Saw you're hiring a .NET Developer.", "Saw you're hiring a .NET Developer."),
    (".NET developer at Acme", ".NET developer at Acme"),
    ("Following up on the .NET role.", "Following up on the .NET role."),
    ("We place .NET engineers", "We place .NET engineers"),
    ("Java, .NET and Go", "Java, .NET and Go"),
    ("roughly .5 FTE", "roughly .5 FTE"),
    ("saw you're hiring a .", "saw you're hiring."),       # emptied placeholders are still tidied
    ("line one.\n.\n, leftover", "line one.\n\nleftover"),
])
def test_tidy_keeps_words_that_start_with_a_dot(raw, expected):
    assert tidy(raw) == expected


def test_template_and_ai_copy_keep_dotnet_titles(make_ctx):
    ctx = make_ctx(offer=OFFER)
    lead = make_lead(signals=[Signal(type="job_posting", title=".NET Developer", posted_at="2026-09-22")])
    out = TemplateWriter({}, ctx).write(lead)
    assert out.messages[0].subject == ".NET developer at Acme Widgets"
    assert "Saw you're hiring a .NET Developer this week." in out.messages[0].body
    assert "the .NET Developer hire" in out.messages[1].body
    assert out.warnings == []
    assert article(".NET Developer") == "a"
    assert clean_body("Hi Jane,\n\nFollowing up on the .NET role.\n\nAny interest?") == \
        "Hi Jane,\n\nFollowing up on the .NET role.\n\nAny interest?"


def test_ai_writer_rejects_non_string_bodies_and_subjects(make_ctx):
    # a list of paragraphs is joined, never rendered as a Python list repr
    data = ai_response(n=2)
    data["emails"][0]["body"] = ["Hi Jane,", "Saw Acme is hiring a Senior Accountant.", "Worth a quick chat?"]
    ctx = ai_ctx(make_ctx, data, sequence=[{"day": 1}, {"day": 3}])
    out = AIWriter({}, ctx).write(make_lead())
    assert out.writer == "ai:fake-model" and out.warnings == []
    assert out.messages[0].body.startswith("Hi Jane,\n\nSaw Acme is hiring a Senior Accountant.\n\nWorth a quick chat?")
    assert "['" not in out.messages[0].body
    # anything else that is not text is a problem -> feedback retry -> template fallback
    for field, value in (("body", {"text": "Hi"}), ("body", ["Hi", 3]), ("subject", ["senior accountant"])):
        bad = ai_response(n=2)
        bad["emails"][0][field] = value
        ctx = ai_ctx(make_ctx, bad, bad, sequence=[{"day": 1}, {"day": 3}])
        out = AIWriter({}, ctx).write(make_lead())
        assert out.writer == "template", (field, value)
        assert f"step 1: {field} must be a plain string" in out.warnings[0]
        assert f"step 1: {field} must be a plain string" in ctx.llm.calls[1]["user"]  # fed back once


def test_guardrails_work_for_non_latin_campaigns(make_ctx):
    ctx = make_ctx(offer=dict(OFFER, language="Russian"), writer={
        "type": "ai", "provider": "openai", "sequence": [{"day": 1}, {"day": 3}],
        "banned_phrases": ["бесплатно"]})
    lead = make_lead(name="Яндекс", domain="yandex.ru",
                     signals=[Signal(type="job_posting", title="Главный бухгалтер", posted_at="2026-09-22")],
                     contact=Contact(first_name="Анна", email="anna@yandex.ru"))
    good = {"emails": [
        {"step": 1, "subject": "бухгалтер в Яндекс",
         "body": "Здравствуйте, Анна!\n\nУвидел, что Яндекс ищет главного бухгалтера.\n\nУдобно обсудить?"},
        {"step": 2, "subject": "", "body": "Анна, добрый день.\n\nНапоминаю про бухгалтера.\n\nИнтересно?"}]}
    ctx.llm = RecordingLLM(good)
    out = AIWriter({}, ctx).write(lead)
    assert out.writer == "ai:fake-model" and out.warnings == [] and len(ctx.llm.calls) == 1
    # inflected company name ("в Яндексе") still counts as a mention
    inflected = msg("Здравствуйте, Анна!\n\nКоманда в Яндексе растёт.\n\nУдобно?", "идея", lead=lead, ctx=ctx)
    assert check_message(inflected, lead, ctx, 0) == []
    # ... and non-Latin copy that ignores the company is still caught
    vague = msg("Здравствуйте, Анна!\n\nЕсть идея.\n\nУдобно?", "идея", lead=lead, ctx=ctx)
    assert any("does not mention" in p for p in check_message(vague, lead, ctx, 0))
    # non-Latin banned phrases are enforced, identical bodies are detected
    spam = msg("Здравствуйте!\n\nЯндекс, это бесплатно.\n\nУдобно?", "идея", lead=lead, ctx=ctx)
    assert 'step 1: uses banned phrase "бесплатно"' in check_message(spam, lead, ctx, 0)
    same = [Message(step=i + 1, day=i + 1, subject="идея" if i == 0 else "",
                    body=append_signoff("Яндекс ищет бухгалтера.", *signoff_for(lead, ctx))) for i in range(2)]
    assert "step 2: same body as step 1" in check_sequence(same, lead, ctx)
    # Latin names next to non-spaced scripts (Japanese particles) still match
    jp = make_lead(name="Acme", domain="acme.co.jp", signals=[])
    assert check_message(msg("Acmeが経理担当者を募集中と拝見しました。", "ご提案", lead=jp, ctx=ctx), jp, ctx, 0) == []


def test_custom_signal_phrase_gets_no_extra_age_phrase(make_ctx):
    lead = make_lead(signals=[Signal(type="job_posting", title="Senior Accountant", posted_at="2026-09-22")])
    cases = {
        "saw your {signal_title} post {signal_age_phrase}": "Saw your Senior Accountant post this week.",
        "I saw you're hiring a {signal_title}.": "I saw you're hiring a Senior Accountant.",
        "congrats on opening the {signal_title} role!": "Congrats on opening the Senior Accountant role!",
    }
    for phrase, opener in cases.items():
        ctx = make_ctx(offer=OFFER, writer={"templates": {"signal_phrases": {"job_posting": phrase}}})
        out = TemplateWriter({}, ctx).write(lead)
        assert out.personalization == opener
        assert out.messages[0].body.split("\n")[2] == opener
    # the built-in phrase still gets its age
    assert TemplateWriter({}, make_ctx(offer=OFFER)).write(lead).personalization == \
        "Saw you're hiring a Senior Accountant this week."


def test_ai_side_fields_pass_guardrails_before_export(make_ctx):
    data = ai_response(n=2, personalization_line="<the opening line of email 1, referencing the signal>",
                       pain_hypothesis="[Company] is stretched")
    ctx = ai_ctx(make_ctx, data, sequence=[{"day": 1}, {"day": 3}])
    out = AIWriter({}, ctx).write(make_lead())
    assert out.writer == "ai:fake-model"
    assert out.personalization == "Saw Acme is hiring a Senior Accountant this week."  # first sentence of email 1
    assert out.hypothesis == ""
    for bad in ("Hi {first_name}, saw the role.", "See https://evil.example/x", "Act now on the Senior Accountant role",
                ["Saw Acme is hiring."], {"line": "x"}):
        ctx = ai_ctx(make_ctx, ai_response(n=2, personalization_line=bad, pain_hypothesis=bad),
                     sequence=[{"day": 1}, {"day": 3}])
        out = AIWriter({}, ctx).write(make_lead())
        assert out.personalization == "Saw Acme is hiring a Senior Accountant this week.", bad
        assert out.hypothesis == "", bad
    # good values pass through, tidied to one line
    ctx = ai_ctx(make_ctx, ai_response(n=2, personalization_line="  **Saw Acme** is hiring\na Senior Accountant. ",
                                       pain_hypothesis="Month-end\nis landing on fewer people."),
                 sequence=[{"day": 1}, {"day": 3}])
    out = AIWriter({}, ctx).write(make_lead())
    assert out.personalization == "Saw Acme is hiring a Senior Accountant."
    assert out.hypothesis == "Month-end is landing on fewer people."


@pytest.mark.parametrize("contact, greeting", [
    (Contact(full_name="Dr. Jane Smith"), "Hi Jane,"),
    (Contact(full_name="Smith, Jane"), "Hi Jane,"),
    (Contact(full_name="Mr John Doe"), "Hi John,"),
    (Contact(full_name="PROF. ANN LEE"), "Hi Ann,"),
    (Contact(full_name="Dr. Smith"), "Hi there,"),               # only a surname left
    (Contact(first_name="Dr.", last_name="Smith"), "Hi there,"),
    (Contact(first_name="Ms", last_name="Jane Roe"), "Hi Jane,"),
    (Contact(first_name="Jane", full_name="Dr. Jane Smith"), "Hi Jane,"),
    (Contact(full_name="jane doe"), "Hi Jane,"),
    (Contact(full_name="Jane Smith, CPA"), "Hi Jane,"),
])
def test_greeting_skips_honorifics_and_reads_last_first(make_ctx, contact, greeting):
    ctx = make_ctx(offer=OFFER)
    lead = make_lead(contact=contact)
    assert TemplateWriter({}, ctx).write(lead).messages[0].body.split("\n")[0] == greeting
    first = greeting[3:-1]
    prompt = build_user_prompt(lead, ctx)
    if first == "there":
        assert "first name unknown" in prompt
    else:
        assert f"- first name: {first}\n" in prompt
    assert nice_name("Dr.") == "" and nice_name("Dr. Jane") == "Jane" and nice_name("Mrs") == ""


def test_booking_link_allowlist_needs_same_host_and_path_boundary(make_ctx):
    lead = make_lead()
    ctx = make_ctx(offer=dict(OFFER, booking_link="https://cal.com"))
    for url in ("https://cal.com.evil.io/phish", "https://cal.comx.io", "https://cal.com@evil.io/x"):
        assert any(p.startswith(f"step 1: link not allowed: {url}")
                   for p in check_message(msg(GOOD_BODY + f" Book: {url}", "acme", lead=lead, ctx=ctx), lead, ctx, 0))
    ok = GOOD_BODY + " Book: https://cal.com/sam and www.cal.com"
    assert check_message(msg(ok, "acme", lead=lead, ctx=ctx), lead, ctx, 0) == []
    ctx2 = make_ctx(offer=dict(OFFER, booking_link="https://calendly.com/sam"))
    bad = check_message(msg(GOOD_BODY + " Book: https://calendly.com/sammy-scam", "acme", lead=lead, ctx=ctx2),
                        lead, ctx2, 0)
    assert bad == ["step 1: link not allowed: https://calendly.com/sammy-scam "
                   "(only offer.booking_link / offer.sender_website)"]
    for url in ("https://calendly.com/sam", "https://calendly.com/sam/15min", "https://calendly.com/sam?month=10",
                "https://Calendly.com/Sam/"):
        assert check_message(msg(GOOD_BODY + f" Book: {url}", "acme", lead=lead, ctx=ctx2), lead, ctx2, 0) == [], url


def test_guardrails_flag_echoed_prompt_examples_but_not_bracketed_links(ctx):
    lead = make_lead()
    for token in ("<the opening line of email 1, referencing the signal>", "<plain text body>",
                  "<one sentence: the problem this signal suggests they have now>"):
        problems = check_message(msg(GOOD_BODY + f" {token}", "acme", lead=lead, ctx=ctx), lead, ctx, 0)
        assert f"step 1: leftover placeholder or markup {token}" in problems
    ok = GOOD_BODY + " Book: <https://cal.com/sam/15min> or mail <sam@northwind.example>, under <2 weeks."
    assert check_message(msg(ok, "acme", lead=lead, ctx=ctx), lead, ctx, 0) == []
