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
