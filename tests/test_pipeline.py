"""Pipeline-level behaviour: export ordering, dedupe, company cooldown, dry-run."""
from __future__ import annotations

import csv
from pathlib import Path

from leadgen.models import Stage
from leadgen.pipeline import Pipeline

ROWS = [
    # company, domain, employees, job title, date, first, last, title, email
    ("Acme Analytics", "acme-t.com", 120, "Senior Accountant", "2 days ago", "Jane", "Doe", "CFO", "jane@acme-t.com"),
    ("Acme Analytics", "acme-t.com", 120, "Senior Accountant", "2 days ago", "Sam", "Roe", "Finance Director", "sam@acme-t.com"),
    ("Beta Freight", "beta-t.com", 300, "Financial Controller", "5 days ago", "Lee", "Chan", "CFO", "lee@beta-t.com"),
]


def _playbook(tmp_path: Path, **over):
    src = tmp_path / "signals.csv"
    with open(src, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Company", "Domain", "Employees", "Job Title", "Date Posted"])
        for r in ROWS:
            w.writerow(r[:5])
    contacts = tmp_path / "contacts.csv"
    with open(contacts, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Company", "Domain", "First Name", "Last Name", "Title", "Email"])
        for r in ROWS:
            w.writerow([r[0], r[1], r[5], r[6], r[7], r[8]])
    pb = {
        "sources": [{"type": "csv", "path": str(src)}],
        "signals": {"match_keywords": ["accountant", "controller"]},
        "buyers": {"titles": ["CFO", "Finance Director"], "max_contacts_per_company": 1},
        "enrichment": {"finders": [{"type": "csv", "path": str(contacts)}],
                       "verifier": {"type": "basic"}, "accept_statuses": ["valid", "risky", "unknown"]},
        "scoring": {"tiers": {"hot": 60, "normal": 30}},
        "offer": {"sender_name": "Alex", "sender_company": "Northbeam", "service": "recruitment"},
        "outbound": {"exporters": [{"type": "csv"}, {"type": "instantly_csv"}]},
        "notify": {"channels": []},
    }
    pb.update(over)
    return pb


def _sheet(res):
    return {r["email"]: r for r in csv.DictReader(open(res.out_dir / "opportunities.csv"))}


def test_run_exports_and_review_sheet_shows_final_stage(make_ctx, tmp_path):
    ctx = make_ctx(**_playbook(tmp_path))
    res = Pipeline(ctx, out_dir=tmp_path / "out").run()
    assert res.counts["exported"] == 2, res.summary()
    sheet = _sheet(res)
    assert sheet["jane@acme-t.com"]["stage"] == Stage.EXPORTED  # csv written after hand-over
    assert (res.out_dir / "instantly_upload.csv").exists()
    # second run: nobody is handed over twice
    res2 = Pipeline(ctx, out_dir=tmp_path / "out").run()
    assert res2.counts["exported"] == 0
    assert res2.counts["written"] == 0            # no copy (no LLM spend) for people already handed over
    assert any("already handed over" in n for ld in res2.leads for n in ld.notes)


def test_colleague_not_emailed_after_unsubscribe_or_within_cooldown(make_ctx, tmp_path):
    ctx = make_ctx(**_playbook(tmp_path))
    res = Pipeline(ctx, out_dir=tmp_path / "out").run()
    jane = next(ld for ld in res.leads if ld.contact and ld.contact.email == "jane@acme-t.com")
    ctx.store.set_stage(jane.id, Stage.LOST, note="unsubscribed")
    ctx.store.suppress("jane@acme-t.com", "email", "unsubscribed")
    # next run would otherwise pick Sam (next best buyer at Acme)
    res2 = Pipeline(ctx, out_dir=tmp_path / "out").run()
    handed = {ld.contact.email for ld in res2.leads if ld.stage == Stage.EXPORTED and ld.contact}
    assert "sam@acme-t.com" not in handed


def test_dry_run_hands_nothing_over(make_ctx, tmp_path):
    ctx = make_ctx(dry_run=True, **_playbook(tmp_path))
    res = Pipeline(ctx, out_dir=tmp_path / "out").run()
    assert res.counts["written"] >= 1 and res.counts["exported"] == 0
    assert not any(ctx.store.was_exported(ld.id) for ld in res.leads)


def test_broken_source_is_reported_not_fatal(make_ctx, tmp_path):
    pb = _playbook(tmp_path)
    pb["sources"] = pb["sources"] + [{"type": "apollo"}]  # no APOLLO_API_KEY in env
    ctx = make_ctx(**pb)
    res = Pipeline(ctx, out_dir=tmp_path / "out").run()
    assert res.counts["sourced"] == 2
    assert any("apollo" in e.lower() for e in res.errors)


def test_guessed_email_needs_valid_by_default(make_ctx, tmp_path):
    """Pattern-guessed addresses that a verifier can only call 'unknown' are never handed over."""
    pb = _playbook(tmp_path)
    contacts = tmp_path / "noemail.csv"
    with open(contacts, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Company", "Domain", "First Name", "Last Name", "Title"])
        w.writerow(["Beta Freight", "beta-t.com", "Lee", "Chan", "CFO"])
    pb["sources"][0]["path"] = str(tmp_path / "signals.csv")
    pb["enrichment"]["finders"] = [{"type": "csv", "path": str(contacts)}, {"type": "pattern"}]
    ctx = make_ctx(**pb)
    res = Pipeline(ctx, out_dir=tmp_path / "out").run()
    lee = next(ld for ld in res.leads if ld.contact and ld.contact.first_name == "Lee")
    assert lee.contact.data.get("email_guessed") and lee.contact.email.endswith("@beta-t.com")
    assert lee.stage != Stage.EXPORTED and not lee.messages
    assert any("guessed" in n for n in lee.notes)
    # opting in (e.g. offline demos) makes it sendable
    pb["enrichment"]["accept_guessed_statuses"] = ["valid", "risky", "unknown"]
    ctx2 = make_ctx(**pb)
    res2 = Pipeline(ctx2, out_dir=tmp_path / "out2").run()
    lee2 = next(ld for ld in res2.leads if ld.contact and ld.contact.first_name == "Lee")
    assert lee2.stage == Stage.EXPORTED


def test_verification_stops_once_enough_contacts_are_deliverable(make_ctx, tmp_path):
    from leadgen import registry
    registry.register("verifier", "counting", "tests.test_pipeline:CountingVerifier")
    CountingVerifier.calls = []
    pb = _playbook(tmp_path)
    pb["enrichment"]["verifier"] = {"type": "counting"}
    pb["enrichment"]["accept_statuses"] = ["valid"]
    ctx = make_ctx(**pb)
    Pipeline(ctx, out_dir=tmp_path / "out").run()
    # Acme has two buyers (Jane CFO, Sam FD); one per company is needed -> Sam is never verified
    assert "sam@acme-t.com" not in CountingVerifier.calls
    assert "jane@acme-t.com" in CountingVerifier.calls


from leadgen.verify.base import VerificationResult, Verifier  # noqa: E402


class CountingVerifier(Verifier):
    name = "counting"
    offline = True
    calls: list = []

    def verify(self, email):
        CountingVerifier.calls.append(email)
        return VerificationResult(email=email, status="valid", provider="counting")


def test_bounced_colleague_does_not_block_company_forever(make_ctx, tmp_path):
    """A bounce is a dead address, not a 'no': after the cooldown a colleague may be tried."""
    from datetime import timedelta
    pb = _playbook(tmp_path)
    pb["outbound"]["company_cooldown_days"] = 30
    ctx = make_ctx(**pb)
    res = Pipeline(ctx, out_dir=tmp_path / "out").run()
    jane = next(ld for ld in res.leads if ld.contact and ld.contact.email == "jane@acme-t.com")
    ctx.store.set_stage(jane.id, Stage.LOST, note="reply: bounce")
    ctx.store.suppress("jane@acme-t.com", "email", "bounced")
    eng = ctx.store.company_engagement("acme-t.com", "test")
    assert "lost_bounce" in eng["stages"] and Stage.LOST not in eng["stages"]
    # 40 days later the cooldown has passed: Sam (next buyer) is handed over
    ctx.today = ctx.today + timedelta(days=40)
    import leadgen.store as store_mod
    real = store_mod.datetime

    class Shifted(real):  # exported_at is stamped with utcnow(); shift "now" back instead
        @classmethod
        def fromisoformat(cls, s):
            return real.fromisoformat(s) - timedelta(days=40)
    store_mod.datetime = Shifted
    try:
        res2 = Pipeline(ctx, out_dir=tmp_path / "out").run()
    finally:
        store_mod.datetime = real
    handed = {ld.contact.email for ld in res2.leads if ld.stage == Stage.EXPORTED and ld.contact}
    assert "sam@acme-t.com" in handed


def test_require_email_false_writes_and_hands_over_without_email(make_ctx, tmp_path):
    pb = _playbook(tmp_path)
    contacts = tmp_path / "noemail.csv"
    with open(contacts, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Company", "Domain", "First Name", "Last Name", "Title", "LinkedIn URL"])
        w.writerow(["Beta Freight", "beta-t.com", "Lee", "Chan", "CFO", "https://linkedin.com/in/leechan"])
    pb["enrichment"]["finders"] = [{"type": "csv", "path": str(contacts)}]
    pb["enrichment"]["verifier"] = None
    pb["outbound"] = {"exporters": [{"type": "json"}], "require_email": False}
    pb["scoring"]["tiers"] = {"hot": 40, "normal": 10}
    ctx = make_ctx(**pb)
    res = Pipeline(ctx, out_dir=tmp_path / "out").run()
    lee = next(ld for ld in res.leads if ld.contact and ld.contact.first_name == "Lee")
    assert lee.messages and not lee.contact.email
