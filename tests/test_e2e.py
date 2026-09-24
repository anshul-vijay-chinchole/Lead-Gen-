"""End-to-end: the offline demo playbook through the real CLI, twice, then replies + stats.

Uses only the shipped sample data (examples/data/*.csv), a temporary database and
a temporary output folder. The sample signals use relative dates ("2 days ago"),
so the expectations below hold whatever day the tests run.
"""
from __future__ import annotations

import csv
import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List

import pytest
import yaml

from leadgen import cli
from leadgen.cli import main
from leadgen.models import ReplyCategory, Stage
from leadgen.playbook import load_playbook
from leadgen.store import Store

REPO = Path(__file__).resolve().parent.parent
DEMO = "playbooks/demo-offline.yaml"
REPLIES = "examples/data/demo_replies.csv"


@pytest.fixture(autouse=True)
def _repo_root(monkeypatch):
    monkeypatch.chdir(REPO)
    for k in ("SENDER_NAME", "SENDER_TITLE", "SENDER_COMPANY", "SENDER_WEBSITE", "BOOKING_LINK"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def ws(tmp_path):
    env = tmp_path / "e.env"
    env.write_text("", encoding="utf-8")
    return {"db": str(tmp_path / "leadgen.db"), "out": tmp_path / "out", "tmp": tmp_path,
            "g": ["--db", str(tmp_path / "leadgen.db"), "--env-file", str(env)]}


def read_csv(path: Path) -> List[Dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def run(ws, *extra: str) -> Path:
    before = set(ws["out"].iterdir()) if ws["out"].exists() else set()
    assert main(["run", "-p", DEMO, "--out", str(ws["out"]), *ws["g"], *extra]) == 0
    new = set(ws["out"].iterdir()) - before
    assert len(new) == 1
    return new.pop()


# Planted rejects in examples/data/demo_signals.csv and the stage that must catch them.
PLANTED_REJECTS = {
    "Staffwise Recruiting": ("icp", "staffing"),                 # competitor
    "Megacorp Global Industries": ("icp", "too large"),
    "Tiny Sprout Studio": ("icp", "too small"),
    "Summit Freight": ("icp", "excluded domain"),                # existing client
    "Cobalt Coffee Roasters": ("signals", "not matching"),       # barista / store manager
    "Old Mill Supply Co": ("signals", "older than 60 days"),
    "Pinecrest Partners": ("signals", "intern"),
}


def test_full_offline_flow(ws, capsys):
    # --- run 1 ---------------------------------------------------------------------
    run1 = run(ws)
    capsys.readouterr()
    summary = json.loads((run1 / "summary.json").read_text(encoding="utf-8"))
    counts = summary["counts"]
    assert counts["sourced"] == 18
    assert counts["hot"] >= 2 and counts["normal"] >= 2
    assert counts["exported"] == counts["outbound_eligible"] > 0
    assert summary["errors"] == []

    # rejected.csv explains every planted reject
    rejected = {r["company"]: r for r in read_csv(run1 / "rejected.csv")}
    assert set(rejected) == set(PLANTED_REJECTS)
    for company, (stage, needle) in PLANTED_REJECTS.items():
        assert rejected[company]["stage"] == stage, company
        assert needle in rejected[company]["reason"], (company, rejected[company]["reason"])

    # opportunities.csv: every qualified company, best first, with reasons
    opp = read_csv(run1 / "opportunities.csv")
    assert len(opp) == counts["qualified"]
    scores = [int(r["score"]) for r in opp]
    assert scores == sorted(scores, reverse=True)
    top = opp[0]
    assert top["company"] == "Brightwave Analytics" and top["tier"] == "hot"
    by_company = {r["company"]: r for r in opp}
    assert by_company["Meridian Foods"]["tier"] == "skip"         # only a generic info@ mailbox

    # upload CSVs: only hot/normal, one person per company, clean copy
    upload = read_csv(run1 / "instantly_upload.csv")
    assert len(upload) == counts["exported"]
    assert len(read_csv(run1 / "smartlead_upload.csv")) == len(upload)
    emails = {r["email"] for r in upload}
    assert "maya.okafor@brightwave-demo.com" in emails             # buyer from the contacts CSV
    assert "priya.natarajan@kestrel-demo.com" in emails            # guessed by the pattern finder
    assert not any(e.split("@")[0] in ("info", "jobs") for e in emails)   # no generic mailboxes
    assert "lena.fischer@quillstone-demo.com" not in emails        # excluded title (assistant)
    assert not any(r["email"].endswith("summitfreight-demo.com") for r in upload)
    for r in upload:
        assert r["tier"] in ("hot", "normal")
        assert r["subject_1"] and r["email_1"].startswith(f"Hi {r['first_name']},")
        for step in ("email_1", "email_2", "email_3", "email_4"):
            body = r[step]
            assert body and "{" not in body and "}" not in body, (r["email"], step)   # no leftover placeholders
            assert "Alex Morgan" in body                                              # signed
        assert r["company_name"] in r["email_1"] or r["personalization"] in r["email_1"]

    # the store knows the run
    store = Store(ws["db"])
    run_id = store.latest_run_id("demo-offline")
    assert run_id == run1.name
    leads = store.leads_for_run(run_id)
    assert {ld.stage for ld in leads if ld.contact and ld.contact.email in emails} == {Stage.EXPORTED}
    store.close()

    # --- run 2: same data, nobody is handed over twice --------------------------------
    run2 = run(ws)
    capsys.readouterr()
    counts2 = json.loads((run2 / "summary.json").read_text(encoding="utf-8"))["counts"]
    assert counts2["qualified"] == counts["qualified"] and counts2["hot"] == counts["hot"]
    assert counts2["exported"] == 0 and counts2["outbound_eligible"] == 0
    assert read_csv(run2 / "instantly_upload.csv") == []
    assert len(read_csv(run2 / "opportunities.csv")) == counts["qualified"]   # the review sheet is complete

    # --- demo report ----------------------------------------------------------------------
    demo_dir = ws["tmp"] / "demo"
    assert main(["demo", "-p", DEMO, "--out", str(demo_dir), "--prospect", "Northbeam Talent", *ws["g"]]) == 0
    capsys.readouterr()
    md = (demo_dir / "demo-northbeam-talent.md").read_text(encoding="utf-8")
    page = (demo_dir / "demo-northbeam-talent.html").read_text(encoding="utf-8")
    assert "Brightwave Analytics" in md and "Brightwave Analytics" in page
    assert "maya.okafor@brightwave-demo.com" not in md                      # masked
    assert len(re.findall(r"^## \d+\. ", md, re.M)) == 5                   # top 5 opportunities
    assert not re.search(r"\{[a-z_]+\}", md)

    # --- replies --------------------------------------------------------------------------
    rep_dir = ws["tmp"] / "replies"
    assert main(["replies", "-p", DEMO, "--file", REPLIES, "--out", str(rep_dir), *ws["g"]]) == 0
    out = capsys.readouterr().out
    assert "Classified 12 replies" in out
    rows = read_csv(rep_dir / "replies_classified.csv")
    got = [(r["from_email"], r["category"]) for r in rows]
    assert got == [
        ("maya.okafor@brightwave-demo.com", ReplyCategory.POSITIVE),
        ("tom@lumenledger-demo.com", ReplyCategory.REFERRAL),
        ("mailer-daemon@kestrel-demo.com", ReplyCategory.BOUNCE),
        ("s.vermeer@harborline-demo.com", ReplyCategory.NEGATIVE),
        ("graham.holt@redfern-demo.com", ReplyCategory.OOO),
        ("rachel.kimura@northpeak-demo.com", ReplyCategory.QUESTION),
        ("marcus.bell@vantage-robotics-demo.com", ReplyCategory.UNSUBSCRIBE),
        ("hannah.price@lumenledger-demo.com", ReplyCategory.POSITIVE),
        ("graham.holt@redfern-demo.com", ReplyCategory.TIMING),
        ("daniel.reyes@brightwave-demo.com", ReplyCategory.OTHER),
        ("rachel.kimura@northpeak-demo.com", ReplyCategory.POSITIVE),
        ("postmaster@northpeak-demo.com", ReplyCategory.OTHER),
    ]
    assert set(ReplyCategory.ALL) == {c for _, c in got}                    # every category covered
    referral = rows[1]
    assert referral["referral_email"] == "hannah.price@lumenledger-demo.com"
    assert "We help teams like Lumen Ledger" in referral["suggested_reply"]

    store = Store(ws["db"])

    def stage(email: str) -> str:
        lead = store.find_lead_by_email(email, "demo-offline")
        assert lead is not None, email
        return lead.stage

    assert stage("maya.okafor@brightwave-demo.com") == Stage.POSITIVE
    assert stage("tom@lumenledger-demo.com") == Stage.REPLIED
    assert stage("hannah.price@lumenledger-demo.com") == Stage.POSITIVE    # the referral became a lead
    assert stage("s.vermeer@harborline-demo.com") == Stage.LOST
    assert stage("priya.natarajan@kestrel-demo.com") == Stage.LOST         # bounced
    assert stage("marcus.bell@vantage-robotics-demo.com") == Stage.LOST    # unsubscribed
    assert stage("graham.holt@redfern-demo.com") == Stage.REPLIED
    assert stage("rachel.kimura@northpeak-demo.com") == Stage.POSITIVE
    for email in ("s.vermeer@harborline-demo.com", "priya.natarajan@kestrel-demo.com",
                  "marcus.bell@vantage-robotics-demo.com"):
        assert store.is_suppressed(email=email)
    assert store.get_verification("priya.natarajan@kestrel-demo.com") == "invalid"
    reasons = sorted(f["reason"] for f in store.due_followups(date.today() + timedelta(days=200), "demo-offline"))
    # one open follow-up per person: Graham's later "next quarter" reply supersedes his OOO one
    assert reasons == ["timing"]
    funnel = store.funnel("demo-offline")
    assert funnel["stages"]["positive"] == 3 and funnel["lost"] == 3
    assert funnel["replies"][ReplyCategory.POSITIVE] == 3
    store.close()

    # --- stats reflect it; a booked call moves the funnel ---------------------------------
    assert main(["mark", "--email", "maya.okafor@brightwave-demo.com", "--stage", "booked", *ws["g"]]) == 0
    capsys.readouterr()
    assert main(["stats", "-p", DEMO, *ws["g"]]) == 0
    stats = capsys.readouterr().out
    assert re.search(r"positive\s+3\b", stats) and re.search(r"booked\s+1\b", stats)
    assert "Lost: 3" in stats and re.search(r"unsubscribe\s+1\b", stats)
    assert run1.name in stats and run2.name in stats

    # --- run 3 after the replies: suppressed people are never exported again ---------------
    run3 = run(ws)
    capsys.readouterr()
    upload3 = {r["email"] for r in read_csv(run3 / "instantly_upload.csv")}
    assert not upload3 & {"s.vermeer@harborline-demo.com", "priya.natarajan@kestrel-demo.com",
                          "marcus.bell@vantage-robotics-demo.com"}
    assert not upload3 & emails                                             # nobody twice


def test_dry_run_then_real_run(ws, capsys):
    dry = run(ws, "--dry-run")
    counts = json.loads((dry / "summary.json").read_text(encoding="utf-8"))["counts"]
    assert counts["exported"] == 0 and counts["written"] > 0   # offline playbook: everything but the hand-over
    real = run(ws)
    capsys.readouterr()
    counts = json.loads((real / "summary.json").read_text(encoding="utf-8"))["counts"]
    assert counts["exported"] > 0                              # the dry run did not "use up" anyone


def test_same_person_under_two_company_records_is_handed_over_once(ws, capsys):
    """A group CFO listed under two companies must get one sequence, not two."""
    tmp = ws["tmp"]
    signals = tmp / "signals.csv"
    signals.write_text(
        "company,domain,location,industry,employees,signal_type,signal_title,signal_date,signal_url,signal_id\n"
        "Acme Group,acme-demo.com,London,Software,300,job_posting,Financial Controller,2 days ago,"
        "https://acme-demo.com/jobs/1,a1\n"
        "Acme UK,acme-uk-demo.com,London,Software,120,job_posting,Senior Financial Analyst,1 day ago,"
        "https://acme-uk-demo.com/jobs/2,a2\n", encoding="utf-8")
    contacts = tmp / "contacts.csv"
    contacts.write_text(
        "company,domain,first_name,last_name,title,email,email_status\n"
        "Acme Group,acme-demo.com,Jane,Doe,CFO,jane.doe@acme-demo.com,valid\n"
        "Acme UK,acme-uk-demo.com,Jane,Doe,CFO,jane.doe@acme-demo.com,valid\n", encoding="utf-8")
    data = yaml.safe_load((REPO / DEMO).read_text(encoding="utf-8"))
    data["sources"][0]["path"] = str(signals)
    data["enrichment"]["finders"] = [{"type": "csv", "path": str(contacts)}]
    pb = tmp / "dup.yaml"
    pb.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    assert main(["run", "-p", str(pb), "--out", str(ws["out"]), *ws["g"]]) == 0
    capsys.readouterr()
    run_dir = next(ws["out"].iterdir())
    leads = json.loads((run_dir / "leads.json").read_text(encoding="utf-8"))
    both = [ld for ld in leads if ld["contact"] and ld["contact"]["email"] == "jane.doe@acme-demo.com"]
    assert len(both) == 2                                   # the setup: two leads, one person
    written = [ld for ld in both if ld["messages"]]
    assert len(written) == 1                                # the writer is not spent on the duplicate
    assert any("same email" in n for ld in both for n in ld["notes"])
    for name in ("instantly_upload.csv", "smartlead_upload.csv"):
        assert [r["email"] for r in read_csv(run_dir / name)] == ["jane.doe@acme-demo.com"], name
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["counts"]["exported"] == 1


def test_every_playbook_loads():
    files = sorted((REPO / "playbooks").glob("*.yaml")) + sorted((REPO / "playbooks" / "templates").glob("*.yaml"))
    names = set()
    for f in files:
        pb = load_playbook(str(f), env={})
        assert pb.name and pb.description, f
        names.add(pb.name)
        # every configured adapter type is known to the registry
        from leadgen import registry

        kinds = [("source", s) for s in pb.sources] + \
                [("finder", s) for s in pb.enrichment.get("finders") or []] + \
                [("exporter", s) for s in pb.outbound.get("exporters") or []] + \
                [("notifier", s) for s in pb.notify.get("channels") or []]
        if pb.enrichment.get("verifier"):
            kinds.append(("verifier", pb.enrichment["verifier"]))
        for kind, cfg in kinds:
            registry.resolve(kind, cfg["type"])
        if pb.writer["type"] == "ai":
            registry.resolve("llm", pb.writer["provider"])
    assert {"demo-offline", "my-agency"} <= names
    templates = {p.stem for p in (REPO / "playbooks" / "templates").glob("*.yaml")}
    assert templates == set(cli.TEMPLATES)


def test_every_playbook_notify_on_is_read_from_the_file(tmp_path):
    """YAML 1.1 reads an unquoted ``on:`` key as boolean True, which silently drops the event
    list (the defaults were used instead). Shipped playbooks + templates quote it."""
    files = sorted((REPO / "playbooks").glob("*.yaml")) + sorted((REPO / "playbooks" / "templates").glob("*.yaml"))
    for f in files:
        text = f.read_text(encoding="utf-8")
        raw = yaml.safe_load(text)["notify"]
        assert "on" in raw and not any(isinstance(k, bool) for k in raw), f.name
        # changing the list in the file changes what the engine alerts on
        edited = re.sub(r'(?m)^  "on": \[[^\]]*\]', '  "on": [run_summary, unsubscribe, negative]', text)
        assert edited != text, f.name
        tmp = tmp_path / f.name
        tmp.write_text(edited, encoding="utf-8")
        pb = load_playbook(str(tmp), env={})
        assert pb.notify["on"] == ["run_summary", "unsubscribe", "negative"], f.name
        assert not any(isinstance(k, bool) for k in pb.notify), f.name


def test_every_playbook_path_resolves_from_repo_root():
    for f in list((REPO / "playbooks").glob("*.yaml")) + list((REPO / "playbooks" / "templates").glob("*.yaml")):
        pb = load_playbook(str(f.relative_to(REPO)), env={})
        for cfg in list(pb.sources) + list(pb.enrichment.get("finders") or []):
            if cfg.get("path") and cfg.get("enabled") is not False:
                assert pb.resolve_path(cfg["path"]).exists(), (f.name, cfg["path"])


def test_sample_data_is_fictional():
    """Sample files only use made-up '-demo' domains (never real companies or people)."""
    for name in ("demo_signals.csv", "demo_contacts.csv", "demo_replies.csv"):
        text = (REPO / "examples" / "data" / name).read_text(encoding="utf-8")
        domains = set(re.findall(r"@([a-z0-9.-]+\.[a-z]{2,})", text)) | \
            set(re.findall(r"https?://([a-z0-9.-]+)", text))
        assert domains, name
        assert all(d.endswith("-demo.com") for d in domains), (name, sorted(domains))
