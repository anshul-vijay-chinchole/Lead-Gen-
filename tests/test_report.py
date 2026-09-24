"""Tests for ``leadgen.report``: demo report (Markdown + HTML), funnel and runs reports."""
from __future__ import annotations

import re
from datetime import date
from typing import Any, List, Optional

from leadgen.models import (Company, Contact, Lead, Message, Reply, ReplyCategory, ScoreBreakdown,
                            Signal, Stage)
from leadgen.report import (demo_report, funnel_report, mask_email, runs_report,
                            select_opportunities, signal_label, slugify, write_demo)
from leadgen.store import Store

OFFER = {"sender_name": "Sam Reed", "sender_title": "Founder", "sender_company": "Signal Labs",
         "sender_website": "signallabs.io", "cta": "Want this list every Monday?",
         "booking_link": "https://cal.com/sam/intro"}


def lead(name: str = "Acme Inc", domain: str = "acme.com", score: int = 80, tier: str = "hot",
         email: Optional[str] = "jane.doe@acme.com", full_name: str = "Jane Doe", title: str = "CFO",
         status: str = "valid", messages: bool = True, contact: bool = True,
         signals: Optional[List[Signal]] = None, **kw: Any) -> Lead:
    if signals is None:
        signals = [Signal(type="job_posting", title="Senior Accountant", posted_at="2026-09-20",
                          url="https://jobs.example.com/acme/123")]
    company = Company(name=name, domain=domain, industry="Manufacturing", employees=120,
                      location="Manchester", country="UK", signals=signals)
    ct = Contact(full_name=full_name, title=title, email=email or "", email_status=status) if contact else None
    msgs = [Message(step=1, day=1, subject="accountant hire at acme",
                    body="Hi Jane,\n\nSaw you're hiring a Senior Accountant.\n\nSam"),
            Message(step=2, day=3, subject="", body="Quick bump.")] if messages else []
    ld = Lead(company=company, contact=ct, score=score, tier=tier, playbook="test",
              breakdown=ScoreBreakdown(reasons=["fresh job post (4 days)", "size fits", "verified email"]),
              messages=msgs, hypothesis="Finance team is stretched after rapid growth.")
    for k, v in kw.items():
        setattr(ld, k, v)
    return ld


# --- helpers -------------------------------------------------------------------------------

def test_mask_email():
    assert mask_email("jane.doe@acme.com") == "j*******@acme.com"
    assert mask_email("  Bob@x.io ") == "B**@x.io"
    assert mask_email("j@acme.com") == "j*@acme.com"
    assert mask_email("") == "" and mask_email(None) == ""  # type: ignore[arg-type]
    assert mask_email("@acme.com") == "*@acme.com"
    assert mask_email("notanemail") == "n*********"


def test_slugify_and_signal_label():
    assert slugify("Acme Recruiting Ltd.") == "acme-recruiting-ltd"
    assert slugify("  Zürich / Café  ") == "zurich-cafe"
    assert slugify("!!!") == ""
    assert signal_label("job_posting") == "Hiring" and signal_label("funding") == "Funding"
    assert signal_label("price_change") == "Price change" and signal_label("") == "Signal"


def test_select_opportunities_prefers_complete_leads_one_per_company():
    written = lead(name="Written", domain="w.com", score=60)
    bare = lead(name="Bare", domain="b.com", score=95, messages=False, contact=False)
    contact_only = lead(name="Contact", domain="c.com", score=90, messages=False)
    dup_low = lead(name="Written", domain="w.com", score=40, messages=False, full_name="Other Person",
                   email="other@w.com")
    picked = select_opportunities([bare, dup_low, contact_only, written], top=5)
    assert [p.company.name for p in picked] == ["Written", "Contact", "Bare"]
    assert picked[0] is written
    assert len(select_opportunities([bare, contact_only, written], top=2)) == 2
    assert len(select_opportunities([bare, contact_only, written], top=0)) == 3


# --- demo report -------------------------------------------------------------------------

def test_demo_report_markdown(make_ctx):
    ctx = make_ctx(offer=OFFER)
    funding = Signal(type="funding", title="Series B ($20M)", posted_at="2026-09-01",
                     url="https://news.example.com/acme-series-b")
    hiring = Signal(type="job_posting", title="Senior Accountant", posted_at="2026-09-20",
                    url="https://jobs.example.com/acme/123", reposted=True)
    a = lead(signals=[hiring, funding])
    b = lead(name="Beta Ltd", domain="beta.io", score=70, tier="normal", email="bo@beta.io",
             full_name="Bo Li", title="Head of Finance", status="risky")
    c = lead(name="Gamma", domain="gamma.io", score=50, tier="skip", contact=False, messages=False)
    md, _ = demo_report([c, b, a], ctx, prospect="Acme Recruiting")
    assert md.startswith("# Live opportunities for Acme Recruiting\n")
    assert "*Prepared 24 September 2026 by Sam Reed, Signal Labs*" in md
    assert "As of 24 September 2026, **3 companies** are showing live buying signals" in md
    assert "decision-maker at **2** of them (1 with a verified email)" in md
    assert "Below are the top 3" in md
    # order: written leads by score, then the one without a contact
    assert md.index("## 1. Acme Inc") < md.index("## 2. Beta Ltd") < md.index("## 3. Gamma")
    assert "## 1. Acme Inc ([acme.com](<https://acme.com>))" in md
    assert "120 employees · Manufacturing · Manchester, UK" in md
    assert ("- **Hiring:** Senior Accountant (4 days ago, re-posted: still open) · "
            "[source](<https://jobs.example.com/acme/123>)") in md
    assert "- **Funding:** Series B ($20M) (23 days ago)" in md
    assert "**Why now:** Finance team is stretched after rapid growth." in md
    assert "**Decision-maker:** Jane Doe, CFO · `j*******@acme.com` (verified)" in md
    assert "`b*@beta.io` (catch-all domain, unconfirmed)" in md
    assert "jane.doe@acme.com" not in md
    assert "**Score:** 80/100 · hot — fresh job post (4 days); size fits; verified email" in md
    assert "> **Subject:** accountant hire at acme" in md
    assert "> Hi Jane,  \n>\n> Saw you're hiring a Senior Accountant.  \n>\n> Sam" in md
    assert "Quick bump" not in md  # only the first email is shown
    assert "**Decision-maker:** not identified yet" in md
    assert "Want this list every Monday? [Book a time](<https://cal.com/sam/intro>)" in md
    assert "Sam Reed, Founder · Signal Labs · signallabs.io" in md
    assert "partially masked" in md


def test_demo_report_unmasked_top_and_defaults(make_ctx):
    ctx = make_ctx(name="recruitment-clients")
    leads = [lead(name=f"Co {i}", domain=f"co{i}.com", email=f"person{i}@co{i}.com", score=60 + i)
             for i in range(8)]
    md, page = demo_report(leads, ctx, mask=False)
    assert md.startswith("# Live opportunities for Recruitment Clients\n")
    assert md.count("\n## ") == 6  # 5 opportunities + "Next step"
    assert "## 1. Co 7" in md and "Co 2" not in md
    assert "`person7@co7.com` (verified)" in md and "partially masked" not in md
    assert "Worth a quick chat?" in md  # default offer.cta
    assert "Book a time" not in md  # no booking link configured
    md_all, _ = demo_report(leads, ctx, top=0)
    assert md_all.count("\n## ") == 9


def test_demo_report_escapes_markdown(make_ctx):
    ctx = make_ctx()
    evil = lead(name="<img src=x onerror=alert(1)> *Bold* [x](javascript:alert(1))",
                signals=[Signal(type="job_posting", title="Dev", url="javascript:alert(1)")])
    md, _ = demo_report([evil], ctx)
    assert re.search(r"(?<!\\)<img", md) is None and "\\<img" in md  # only the escaped form
    assert "\\*Bold\\*" in md and "\\[x\\]" in md
    assert "(<javascript:" not in md and "[source]" not in md  # unsafe URL never linked
    assert "Dev (date unknown)" in md  # no posted_at / first_seen


def test_demo_report_html(make_ctx):
    ctx = make_ctx(offer=OFFER)
    evil = lead(name='Evil "Co" <script>alert(1)</script>', domain="evil.com",
                signals=[Signal(type="custom", title="<b>New</b> & shiny", posted_at="2026-09-24",
                                url="javascript:alert(document.cookie)"),
                         Signal(type="news", title="Covered in press", url="https://news.example.com/x?a=1&b=2")],
                hypothesis="They <need> help & fast")
    evil.messages[0] = Message(step=1, day=1, subject="re: <script>", body="Hi <Jane>,\n\nLine 2 & more")
    _, page = demo_report([evil, lead(domain="acme.com")], ctx, prospect='Prospect "Q" <Ltd>')
    assert page.startswith("<!DOCTYPE html>") and page.rstrip().endswith("</html>")
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in page
    assert "<style>" in page and "@media (max-width:600px)" in page
    assert "<script" not in page  # no scripts at all, every dynamic value escaped
    assert "<link" not in page and "src=" not in page  # standalone: no external assets
    assert "<title>Live opportunities for Prospect &quot;Q&quot; &lt;Ltd&gt;</title>" in page
    assert "Evil &quot;Co&quot; &lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "&lt;b&gt;New&lt;/b&gt; &amp; shiny" in page and "· today" in page
    assert "javascript:" not in page
    assert 'href="https://news.example.com/x?a=1&amp;b=2"' in page
    assert "They &lt;need&gt; help &amp; fast" in page
    assert "Subject: re: &lt;script&gt;" in page
    assert "Hi &lt;Jane&gt;,\n\nLine 2 &amp; more" in page
    assert "j*******@acme.com" in page and "jane.doe@acme.com" not in page
    assert '<span class="tier hot">hot</span>' in page and "80/100" in page
    assert 'class="btn" href="https://cal.com/sam/intro"' in page
    assert '<a href="https://signallabs.io"' in page
    assert "<strong>2 companies</strong>" in page
    # every href is http(s)
    assert all(h.startswith(("https://", "http://")) for h in re.findall(r'href="([^"]*)"', page))


def test_demo_report_no_leads(make_ctx):
    ctx = make_ctx(offer=OFFER)
    md, page = demo_report([], ctx, prospect="Acme")
    assert "# Live opportunities for Acme" in md
    assert "**No live opportunities yet.**" in md and "signals.max_age_days" in md
    assert "No live opportunities yet." in page and "<code>signals.max_age_days</code>" in page
    assert "Book a time" in md and "partially masked" not in md
    assert "Want this list every Monday?" in page


def test_demo_report_leads_without_signals_or_contacts(make_ctx):
    ctx = make_ctx()
    bare = lead(signals=[], contact=False, messages=False, hypothesis="")
    md, page = demo_report([bare], ctx)
    assert "As of 24 September 2026, **1 company** fits the profile." in md
    assert "decision-maker at **0** of them (0 with a verified email)" in md
    assert "Below is the top one" in md
    assert "**Signals**" not in md and "**Why now:**" not in md and "Suggested first email" not in md
    assert "Not identified yet" in page


def test_demo_report_signal_ages_and_limits(make_ctx):
    ctx = make_ctx()
    sigs = [Signal(type="job_posting", title=f"Role {i}", posted_at=date(2026, 9, 24 - i)) for i in range(5)]
    sigs.append(Signal(type="expansion", title="New office"))  # undated
    md, _ = demo_report([lead(signals=sigs)], ctx)
    assert "Role 0 (today)" in md and "Role 1 (1 day ago)" in md and "Role 2 (2 days ago)" in md
    assert "Role 3" not in md and "…and 3 more" in md


def test_demo_report_undated_signal(make_ctx):
    md, _ = demo_report([lead(signals=[Signal(type="expansion", title="New office")])], make_ctx())
    assert "**Expansion:** New office (date unknown)" in md


def test_write_demo(make_ctx, tmp_path):
    ctx = make_ctx(offer=OFFER)
    md_path, html_path = write_demo([lead()], ctx, tmp_path / "demos")
    assert md_path == tmp_path / "demos" / "demo.md" and html_path == tmp_path / "demos" / "demo.html"
    assert md_path.read_text(encoding="utf-8").startswith("# Live opportunities for Test")
    assert "<!DOCTYPE html>" in html_path.read_text(encoding="utf-8")
    md2, html2 = write_demo([lead()], ctx, tmp_path, prospect="Acme Recruiting Ltd.", top=1, mask=False)
    assert md2.name == "demo-acme-recruiting-ltd.md" and html2.name == "demo-acme-recruiting-ltd.html"
    assert "jane.doe@acme.com" in md2.read_text(encoding="utf-8")
    md3, _ = write_demo([], ctx, tmp_path, prospect="???")
    assert md3.name == "demo.md"


# --- funnel / runs -----------------------------------------------------------------------

def seeded_store() -> Store:
    s = Store(":memory:")
    run = s.start_run("p", {"dry_run": False})
    tiers = ["hot", "normal", "skip"]
    for i in range(10):
        ld = Lead(company=Company(name=f"C{i}", domain=f"c{i}.com"),
                  contact=Contact(full_name="A B", email=f"a@c{i}.com"), playbook="p", run_id=run,
                  stage=Stage.READY if i < 8 else Stage.QUALIFIED, tier=tiers[i % 3], score=50 + i)
        s.save_lead(ld)
        if i < 5:
            s.mark_exported(ld.id, "instantly")
        if i < 3:
            s.set_stage(ld.id, Stage.REPLIED)
        if i < 2:
            s.set_stage(ld.id, Stage.POSITIVE)
        if i == 0:
            s.set_stage(ld.id, Stage.BOOKED)
        if i == 9:
            s.set_stage(ld.id, Stage.LOST)
    for cat in (ReplyCategory.POSITIVE, ReplyCategory.POSITIVE, ReplyCategory.OOO, "weird"):
        s.save_reply(Reply(from_email="a@c0.com", body="x", category=cat), "p")
    s.finish_run(run, {"sourced": 40, "with_signal": 30, "qualified": 10, "enriched": 9, "verified": 8,
                       "hot": 4, "normal": 3, "skip": 3, "written": 8, "exported": 5})
    # another playbook that must not leak into "p"
    other = Lead(company=Company(name="X", domain="x.com"), playbook="other", stage=Stage.READY)
    s.save_lead(other)
    return s


def _row(text: str, label: str) -> List[str]:
    for line in text.splitlines():
        parts = line.split()
        if parts and parts[0] == label:
            return parts
    raise AssertionError(f"no row {label!r} in:\n{text}")


def test_funnel_report():
    s = seeded_store()
    out = funnel_report(s, "p")
    assert out.startswith("Funnel: p (all time)\nLeads tracked: 10\n")
    assert _row(out, "sourced") == ["sourced", "10", "-", "-"]
    # cumulative funnel: a READY lead also counts as enriched + verified
    assert _row(out, "enriched") == ["enriched", "8", "80.0%", "-"]
    assert _row(out, "ready") == ["ready", "8", "100.0%", "-"]
    assert _row(out, "exported") == ["exported", "5", "62.5%", "100.0%"]
    assert _row(out, "replied") == ["replied", "3", "60.0%", "60.0%"]
    assert _row(out, "positive") == ["positive", "2", "66.7%", "40.0%"]
    assert _row(out, "booked") == ["booked", "1", "50.0%", "20.0%"]
    assert _row(out, "won") == ["won", "0", "0.0%", "0.0%"]
    assert re.search(r"reply rate\s+replied / exported\s+60\.0%", out)
    assert re.search(r"positive rate\s+positive / exported\s+40\.0%", out)
    assert re.search(r"booking rate\s+booked / positive\s+50\.0%", out)
    assert re.search(r"win rate\s+won / booked\s+0\.0%", out)
    assert "Lost: 1" in out
    assert re.search(r"positive\s+2\s+50\.0%\n\s+ooo\s+1\s+25\.0%\n\s+weird\s+1\s+25\.0%", out)
    assert re.search(r"hot\s+4\s+40\.0%", out) and re.search(r"skip\s+3\s+30\.0%", out)
    # columns are aligned: every stage row has the same length
    stage_block = out.split("\n\n")[1].splitlines()  # header, underline, one row per stage
    stage_lines = stage_block[2:]
    assert [ln.split()[0] for ln in stage_lines] == list(Stage.ORDER)
    assert len({len(ln) for ln in stage_block}) == 1


def test_funnel_report_zero_divisions_and_filters():
    s = Store(":memory:")
    s.save_lead(Lead(company=Company(name="A", domain="a.com"), playbook="p", stage=Stage.QUALIFIED))
    out = funnel_report(s, "p")
    assert _row(out, "enriched") == ["enriched", "0", "0.0%", "-"]
    assert _row(out, "exported") == ["exported", "0", "-", "-"]  # previous stage is 0
    assert _row(out, "replied") == ["replied", "0", "-", "-"]
    assert re.search(r"reply rate\s+replied / exported\s+-", out)
    assert "Replies by category\n  none yet" in out
    assert "No leads recorded yet" in funnel_report(Store(":memory:"), "p")
    future = funnel_report(seeded_store(), "p", since="2999-01-01")
    assert "(since 2999-01-01)" in future and "Leads tracked: 0" in future
    past = funnel_report(seeded_store(), "p", since=date(2000, 1, 1))
    assert "Leads tracked: 10" in past
    assert "Funnel: all playbooks (all time)\nLeads tracked: 11" in funnel_report(seeded_store(), None)


def test_runs_report():
    s = seeded_store()
    assert runs_report(Store(":memory:"), "p") == "No runs recorded yet for p.\n"
    s.start_run("p", {"dry_run": True, "limit": 5})  # unfinished
    out = runs_report(s, "p")
    lines = out.splitlines()
    assert lines[0] == "Recent runs: p (last 2)"
    header = lines[2].split()
    assert header[:3] == ["run", "started", "sourced"] and header[-2:] == ["took", "notes"]
    unfinished, done = lines[4], lines[5]
    assert "unfinished, dry-run, limit 5" in unfinished and " - " in unfinished
    cells = done.split()
    assert cells[3:12] == ["40", "30", "10", "9", "8", "4", "3", "8", "5"]
    assert re.match(r"\d+s$", cells[12])
    assert len(runs_report(s, "p", limit=1).splitlines()) == 5
    all_pb = runs_report(s, None)
    assert "Recent runs: all playbooks" in all_pb and "playbook" in all_pb.splitlines()[2]


def test_demo_report_markdown_links_cannot_break_out(make_ctx):
    ctx = make_ctx(offer={"booking_link": "https://cal.com/me?x=<b>"})
    sig = Signal(type="news", title="Press", posted_at="2026-09-23",
                 url="https://news.example.com/a b/<script>alert(1)</script>")
    md, page = demo_report([lead(signals=[sig])], ctx)
    assert "<script>" not in md and "%3Cscript%3E" in md and "a%20b" in md
    assert "[Book a time](<https://cal.com/me?x=%3Cb%3E>)" in md
    assert "<script>" not in page


def test_demo_report_counts_use_signal_companies_as_base(make_ctx):
    with_signal = lead(domain="a.com")
    no_signal = lead(name="NoSig", domain="n.com", signals=[], email="x@n.com")
    md, _ = demo_report([with_signal, no_signal], make_ctx())
    assert "**1 company** is showing live buying signals" in md
    assert "decision-maker at **1** of them (1 with a verified email)" in md
