"""CLI tests for the lead-delivery business: deliver, clients, doctor, suppress --client,
--budget, the delivery-mode gate on replies / serve / followups, and the delivery-mode
changes to validate / adapters / run / demo.

Every test runs ``main([...])`` from its own ``tmp_path`` working directory with a
temporary database, an empty (or test-only) env file and ``cli.make_http``
replaced by a ``FakeHttp``, so no test can reach the real network. Job dates are
written relative to the real ``date.today()`` because the CLI runs "as of today".
"""
from __future__ import annotations

import csv
import re
import shlex
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
import yaml

from leadgen import cli
from leadgen.cli import main, suppression_value
from leadgen.delivery.client import client_playbook, load_client
from leadgen.delivery.ledger import Ledger
from leadgen.store import Store
from tests.fakes import FakeHttp

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "clients" / "_template.yaml"

# Every env var an adapter / playbook here reads - cleared so a developer's real keys never leak in.
_ENV_VARS = ("THEIRSTACK_API_KEY", "ADZUNA_APP_ID", "ADZUNA_APP_KEY", "APOLLO_API_KEY", "APIFY_TOKEN",
             "HUNTER_API_KEY", "MILLIONVERIFIER_API_KEY", "ZEROBOUNCE_API_KEY", "NEVERBOUNCE_API_KEY",
             "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "INSTANTLY_API_KEY", "SMARTLEAD_API_KEY",
             "SLACK_WEBHOOK_URL", "LEADGEN_WEBHOOK_URL", "LEADGEN_EXPORT_WEBHOOK_URL",
             "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_SERVICE_ACCOUNT_JSON", "LEADGEN_WEBHOOK_TOKEN",
             "SENDER_NAME", "SENDER_TITLE", "SENDER_COMPANY", "SENDER_WEBSITE", "SENDER_EMAIL", "BOOKING_LINK",
             "INSTANTLY_CAMPAIGN_ID", "SMARTLEAD_CAMPAIGN_ID", "LEADGEN_PLUGINS")

MV_URL = "https://api.millionverifier.com/api/v3/"
MV_KEY = "mv-secret-key-123"


def ago(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


TODAY = date.today().isoformat()

JOB_HEADER = ["company", "domain", "location", "industry", "employees", "signal_type", "signal_title",
              "signal_date", "signal_url", "signal_id"]


def job_rows() -> List[list]:
    return [
        ["Acme Corp", "acme-dlv.example", "Austin, TX", "Software", 120, "job_posting", "Senior Accountant",
         ago(2), "https://acme-dlv.example/jobs/1", "a-1"],
        ["Acme Corp", "acme-dlv.example", "Austin, TX", "Software", 120, "job_posting", "Financial Controller",
         ago(4), "https://acme-dlv.example/jobs/2", "a-2"],
        ["Beta LLC", "beta-dlv.example", "Tulsa, OK", "Logistics", 300, "job_posting", "Staff Accountant",
         ago(3), "https://beta-dlv.example/careers/staff-accountant", "b-1"],
        ["Gamma Inc", "gamma-dlv.example", "Dallas, TX", "Software", 80, "job_posting", "Accountant",
         ago(1), "https://gamma-dlv.example/careers/acct", "g-1"],
        # too old for a 7-day window
        ["Delta Co", "delta-dlv.example", "Houston, TX", "Retail", 60, "job_posting", "Payroll Accountant",
         ago(23), "https://delta-dlv.example/jobs/9", "d-1"],
        # funding news only: never delivered in a hiring report
        ["Zeta Capital", "zeta-dlv.example", "Austin, TX", "Finance", 150, "funding", "Raised a Series A",
         ago(4), "https://zeta-dlv.example/news", "z-1"],
        # a role the client does not fill
        ["Eta Systems", "eta-dlv.example", "Austin, TX", "Software", 200, "job_posting", "Software Engineer",
         ago(2), "https://eta-dlv.example/jobs/5", "h-1"],
    ]


CONTACT_HEADER = ["company", "domain", "first_name", "last_name", "title", "email", "email_status", "linkedin"]
CONTACTS = [
    ["Acme Corp", "acme-dlv.example", "Casey", "Money", "CFO", "casey@acme-dlv.example", "valid",
     "https://www.linkedin.com/in/casey-money"],
    ["Beta LLC", "beta-dlv.example", "Robin", "Hire", "Head of Talent", "robin@beta-dlv.example", "catch-all", ""],
    ["Gamma Inc", "gamma-dlv.example", "Pat", "Numbers", "Controller", "", "",
     "https://www.linkedin.com/in/pat-numbers"],
    ["Zeta Capital", "zeta-dlv.example", "Zoe", "Cash", "CFO", "zoe@zeta-dlv.example", "valid", ""],
]
# no provider status: every address needs a verifier check (so a paid verifier is called per email)
UNCHECKED = [[*row[:6], "", row[7]] for row in CONTACTS]
DELIVERED = ["Acme Corp", "Beta LLC", "Gamma Inc"]     # the companies in a first delivery (sorted)


# --- workspace ------------------------------------------------------------------------------------

@dataclass
class WS:
    root: Path
    http: FakeHttp

    @property
    def base(self) -> Path:
        return self.root / "base"

    @property
    def playbook_path(self) -> Path:
        return self.base / "base.yaml"

    @property
    def db(self) -> Path:
        return self.root / "t.db"

    @property
    def pb_db(self) -> Path:
        """The base playbook's storage.path (used when --db is not given)."""
        return self.root / "data" / "pb.db"

    @property
    def env_file(self) -> Path:
        return self.root / "test.env"

    def g(self) -> List[str]:
        return ["--db", str(self.db), "--env-file", str(self.env_file)]

    def write_csv(self, name: str, header: List[str], rows: List[list]) -> Path:
        path = self.base / name
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)
        return path

    def env(self, **values: str) -> None:
        self.env_file.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")

    def playbook(self, verifier: Optional[Dict[str, Any]] = None, **extra: Any) -> Path:
        data: Dict[str, Any] = {
            "name": "cli-dlv-base",
            "mode": "delivery",
            "sources": [{"type": "csv", "label": "jobs", "path": str(self.base / "jobs.csv")}],
            "enrichment": {"finders": [{"type": "csv", "path": str(self.base / "contacts.csv")},
                                       {"type": "pattern"}],
                           "verifier": verifier or {"type": "basic"}},
            "scoring": {"tiers": {"hot": 60, "normal": 20}},
            "buyers": {"titles": ["CFO", "Controller", "Head of Talent"]},
            "delivery": {"sender_name": "Alex Morgan"},
            "notify": {"channels": [{"type": "console"}], "on": ["run_summary"]},
            "storage": {"path": str(self.pb_db)},
        }
        data.update(extra)
        self.playbook_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return self.playbook_path

    def client(self, name: str = "acme", **settings: Any) -> Path:
        data: Dict[str, Any] = {
            "display_name": "Acme Staffing Ltd",
            "playbook": str(self.playbook_path),
            "contact": {"name": "Sam Lee", "email": "sam@acme-staffing-demo.example"},
            "roles": ["accountant", "controller"],
            "buyer_titles": ["CFO", "Controller", "Head of Talent"],
            "company_size": {"min": 20},
            "leads_per_week": 10,
        }
        data.update(settings)
        folder = self.root / "clients"
        folder.mkdir(exist_ok=True)
        path = folder / f"{name}.yaml"
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return path

    def out(self) -> str:
        return str(self.root / "out" / "{client}" / "{date}")

    def folder(self, client: str = "acme", suffix: str = "") -> Path:
        return self.root / "out" / client / (TODAY + suffix)


@pytest.fixture
def ws(tmp_path, monkeypatch) -> WS:
    monkeypatch.chdir(tmp_path)
    for k in _ENV_VARS:
        monkeypatch.delenv(k, raising=False)
    http = FakeHttp()
    monkeypatch.setattr(cli, "make_http", lambda: http)
    w = WS(root=tmp_path, http=http)
    w.base.mkdir()
    w.write_csv("jobs.csv", JOB_HEADER, job_rows())
    w.write_csv("contacts.csv", CONTACT_HEADER, CONTACTS)
    w.playbook()
    w.client()
    w.env()
    return w


def deliver_cmd(ws: WS, *extra: str) -> int:
    return main(["deliver", "--client", "acme", "--out", ws.out(), *ws.g(), *extra])


def read_csv(path: Path) -> List[Dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def ledger_summary(db: Path, client: str = "acme") -> Dict[str, Any]:
    store = Store(str(db))
    try:
        return Ledger(store).summary(client)
    finally:
        store.close()


def mv_routes(http: FakeHttp) -> None:
    http.add("GET", MV_URL, fn=lambda call: {
        "email": call["params"]["email"], "quality": "good", "result": "ok", "resultcode": 1, "subresult": "ok",
        "free": False, "role": False, "didyoumean": "", "credits": 9999, "executiontime": 1, "error": "",
        "livemode": True})


def mv_workspace(ws: WS, **playbook_extra: Any) -> None:
    ws.write_csv("contacts.csv", CONTACT_HEADER, UNCHECKED)
    ws.playbook(verifier={"type": "millionverifier"}, **playbook_extra)
    ws.env(MILLIONVERIFIER_API_KEY=MV_KEY)
    mv_routes(ws.http)


# =============================================================================
# deliver
# =============================================================================

def test_deliver_prints_client_files_then_internal_files_then_qa_then_next(ws, capsys):
    assert deliver_cmd(ws) == 0
    captured = capsys.readouterr()
    out = captured.out
    folder = ws.folder()
    names = {ext: f"acme-hiring-signals-{TODAY}.{ext}" for ext in ("csv", "xlsx", "html")}
    for name in names.values():
        assert (folder / name).is_file(), name
        assert out.count(name) == 1, name          # listed once (not again inside the QA summary)
    for name in ("qa.txt", "qa.json", "not_delivered.csv"):
        assert (folder / "_internal" / name).is_file(), name

    positions = [out.index(s) for s in ("Delivering the Hiring Signal Report for Acme Staffing Ltd (acme)",
                                        f"Files for the client in {folder}",
                                        f"Internal files - for you, not the client - in {folder / '_internal'}",
                                        "Delivery QA - Acme Staffing Ltd (acme)",
                                        f"Next: send the files in {folder} to Sam Lee <sam@acme-staffing-demo.example>"
                                        f" at Acme Staffing Ltd")]
    assert positions == sorted(positions)
    assert "qa.txt" in out and "not_delivered.csv" in out and "the pipeline run" in out
    assert "delivered .................. 3 (target 10)" in out
    assert "API usage: paid lookups 0 (no cap)" in out          # usage + cost after every delivery
    # the pipeline's own console run summary is not printed on top of the QA summary
    assert "[RUN SUMMARY]" not in out and "sourced companies" not in out
    assert captured.err == ""

    assert sorted(r["Company"] for r in read_csv(folder / names["csv"])) == DELIVERED
    summary = ledger_summary(ws.db)
    assert summary["company"] == 3 and summary["deliveries"] == 1 and summary["last_delivery"] == TODAY
    # the client file on disk is untouched (the console channel was only dropped in memory)
    assert "notify" not in yaml.safe_load((ws.root / "clients" / "acme.yaml").read_text(encoding="utf-8"))


def test_deliver_default_folder_comes_from_the_client_file(ws, capsys):
    assert main(["deliver", "--client", "acme", *ws.g()]) == 0
    folder = ws.root / "deliveries" / "acme" / TODAY
    assert (folder / f"acme-hiring-signals-{TODAY}.csv").is_file()
    assert f"Files for the client in deliveries/acme/{TODAY}" in capsys.readouterr().out


def test_deliver_same_week_again_has_nothing_new_and_exits_1(ws, capsys):
    assert deliver_cmd(ws) == 0
    first = (ws.folder() / f"acme-hiring-signals-{TODAY}.csv").read_bytes()
    capsys.readouterr()
    assert deliver_cmd(ws) == 1
    captured = capsys.readouterr()
    assert "error: no leads were delivered" in captured.err
    assert "Nothing to send this time: no new leads for Acme Staffing Ltd" in captured.out
    assert "Next: send the files" not in captured.out
    assert "delivered .................. 0 (target 10)" in captured.out
    assert "duplicates removed ......... 3" in captured.out
    # the real delivery is never overwritten: the empty run went to <folder>-2
    assert (ws.folder() / f"acme-hiring-signals-{TODAY}.csv").read_bytes() == first
    assert (ws.folder(suffix="-2") / f"acme-hiring-signals-{TODAY}.csv").is_file()


def test_deliver_dry_run_writes_preview_files_and_records_nothing(ws, capsys):
    assert deliver_cmd(ws, "--dry-run") == 0
    out = capsys.readouterr().out
    folder = ws.folder()
    assert (folder / f"acme-hiring-signals-{TODAY}-PREVIEW.csv").is_file()
    assert not (folder / f"acme-hiring-signals-{TODAY}.csv").exists()
    assert "DRY RUN" in out and "PREVIEW files - not for the client" in out
    assert f"Next: this was a preview - check the files, then make the real delivery: leadgen deliver " \
           f"--client acme --db {shlex.quote(str(ws.db))} --out {shlex.quote(ws.out())}" in out
    assert "Next: send the files" not in out
    assert ledger_summary(ws.db)["items"] == 0
    # ...so the real delivery still gets every lead
    assert deliver_cmd(ws) == 0
    assert "delivered .................. 3 (target 10)" in capsys.readouterr().out


def test_preview_then_delivery_the_folder_to_send_holds_no_preview(ws, capsys):
    """The documented flow (README): --dry-run, then the real delivery the same day. The
    preview skips the paid checker, so it ranks differently and can hold a lead the real
    delivery holds back (not recorded) - the folder the CLI says to send must not hold it."""
    ws.write_csv("contacts.csv", CONTACT_HEADER, UNCHECKED)
    ws.playbook(verifier={"type": "millionverifier"})
    ws.env(MILLIONVERIFIER_API_KEY=MV_KEY)
    good = {"robin@beta-dlv.example", "casey@acme-dlv.example"}     # the checker confirms only these two
    ws.http.add("GET", MV_URL, fn=lambda call: {
        "email": call["params"]["email"], "quality": "good",
        "result": "ok" if call["params"]["email"] in good else "unknown",
        "resultcode": 1 if call["params"]["email"] in good else 5, "subresult": "", "free": False,
        "role": False, "didyoumean": "", "credits": 999, "executiontime": 1, "error": "", "livemode": True})
    ws.client(leads_per_week=2)
    assert deliver_cmd(ws, "--dry-run") == 0
    folder = ws.folder()
    preview_rows = read_csv(folder / f"acme-hiring-signals-{TODAY}-PREVIEW.csv")
    capsys.readouterr()
    assert deliver_cmd(ws) == 0
    out = capsys.readouterr().out
    sendable = sorted(p.name for p in folder.iterdir() if p.name != "_internal")
    assert sendable == sorted(f"acme-hiring-signals-{TODAY}.{ext}" for ext in ("csv", "html", "xlsx"))
    assert f"Next: send the files in {folder} to Sam Lee" in out
    delivered = {r["Company"] for r in read_csv(folder / f"acme-hiring-signals-{TODAY}.csv")}
    # next time: whatever comes was never in a file the client was told to take
    capsys.readouterr()
    assert deliver_cmd(ws) in (0, 1)
    later = {r["Company"] for r in read_csv(ws.folder(suffix="-2") / f"acme-hiring-signals-{TODAY}.csv")}
    assert not later & delivered
    assert {r["Company"] for r in preview_rows} != delivered       # the preview really was different


def test_deliver_next_line_names_only_this_deliverys_files_when_the_folder_holds_others(ws, capsys):
    folder = ws.folder()
    folder.mkdir(parents=True)
    (folder / "old-list.csv").write_text("company\nSomeone Else\n", encoding="utf-8")
    (folder / ".DS_Store").write_bytes(b"")
    assert deliver_cmd(ws) == 0
    out = capsys.readouterr().out
    assert (folder / f"acme-hiring-signals-{TODAY}.csv").is_file()
    assert "Next: send the files in" not in out
    assert ("Next: send ONLY the 3 files listed above to Sam Lee <sam@acme-staffing-demo.example> at Acme "
            f"Staffing Ltd. {folder} also holds old-list.csv: not part of this delivery") in out
    assert ".DS_Store" not in out


@pytest.mark.parametrize("flag, calls", [(None, 4), ("1", 1), ("2", 2), ("0", 4)])
def test_deliver_budget_flag_reaches_the_meter(ws, capsys, flag, calls):
    mv_workspace(ws)
    extra = ["--budget", flag] if flag is not None else []
    assert deliver_cmd(ws, *extra) == 0
    out = capsys.readouterr().out
    mv = [c for c in ws.http.calls if "millionverifier.com" in c["url"]]
    assert len(mv) == calls
    assert all(c["params"]["api"] == MV_KEY and "@" in c["params"]["email"] for c in mv)
    assert MV_KEY not in out
    if flag in ("1", "2"):
        assert f"at most {flag} paid lookup(s)" in out
        assert f"API usage: paid lookups {calls}/{flag}" in out
        assert f"WARNING: Paid-lookup budget reached ({calls} of {flag} paid lookups used)" in out
    else:
        assert f"API usage: paid lookups {calls} (no cap)" in out and "budget reached" not in out
    assert f"verifier millionverifier (paid): {calls} call" in out


def test_deliver_budget_flag_overrides_the_client_file(ws, capsys):
    mv_workspace(ws)
    ws.client(budget={"max_paid_lookups": 3})
    assert deliver_cmd(ws, "--budget", "1") == 0
    assert len(ws.http.calls) == 1
    assert "paid lookups 1/1" in capsys.readouterr().out


def test_deliver_dry_run_makes_no_paid_calls_even_with_a_paid_verifier(ws, capsys):
    mv_workspace(ws)
    assert deliver_cmd(ws, "--dry-run") == 0
    assert ws.http.calls == []
    assert "API usage: paid lookups 0" in capsys.readouterr().out


def test_deliver_negative_budget_is_a_usage_error(ws, capsys):
    assert deliver_cmd(ws, "--budget", "-1") == 2
    err = capsys.readouterr().err
    assert err.startswith("error: --budget must be 0 or more") and "Traceback" not in err
    assert not (ws.root / "out").exists() and not ws.db.exists()
    assert deliver_cmd(ws, "--budget", "lots") == 2       # argparse: not a number


def test_deliver_unknown_and_invalid_clients_are_friendly(ws, capsys):
    assert main(["deliver", "--client", "acmee", *ws.g()]) == 2
    err = capsys.readouterr().err
    assert "client 'acmee' not found" in err and "Did you mean 'acme'?" in err and "Traceback" not in err
    ws.client("broken", leads_per_week="ten", locatons=["Texas"])
    assert main(["deliver", "--client", "broken", *ws.g()]) == 2
    err = capsys.readouterr().err
    assert "leads_per_week" in err and "locatons" in err and "locations" in err
    assert main(["deliver", *ws.g()]) == 2                 # --client is required
    assert not ws.db.exists()                              # nothing was opened


def test_deliver_missing_base_playbook_is_friendly(ws, capsys):
    ws.client(playbook=str(ws.root / "nope.yaml"))
    assert main(["deliver", "--client", "acme", *ws.g()]) == 2
    err = capsys.readouterr().err
    assert "base playbook" in err and "nope.yaml" in err and "Traceback" not in err


def test_deliver_every_source_failed_exits_1(ws, capsys):
    ws.playbook(sources=[{"type": "csv", "label": "jobs", "path": str(ws.base / "missing.csv")}])
    assert deliver_cmd(ws) == 1
    captured = capsys.readouterr()
    assert "error: every source failed (1 of 1)" in captured.err
    assert "file not found" in captured.out            # the QA warnings name the source error


def test_deliver_dry_run_with_only_network_sources_explains_the_empty_file(ws, capsys):
    ws.playbook(sources=[{"type": "adzuna", "countries": ["us"], "queries": ["accountant"]}])
    assert deliver_cmd(ws, "--dry-run") == 1
    captured = capsys.readouterr()
    assert "every source uses the network and --dry-run skips them" in captured.out
    assert "error: no leads were delivered" in captured.err
    assert ws.http.calls == []


def test_deliver_dry_run_with_an_offline_source_that_finds_nothing(ws, capsys):
    ws.write_csv("jobs.csv", JOB_HEADER, [row for row in job_rows() if row[0] == "Delta Co"])   # too old
    assert deliver_cmd(ws, "--dry-run") == 1
    captured = capsys.readouterr()
    assert "every source uses the network" not in captured.out      # the offline source did run
    assert "no signal matching [...] in last 7 days" in captured.out
    assert "error: no leads were delivered" in captured.err and "every source failed" not in captured.err


def test_deliver_without_db_uses_the_client_playbooks_database(ws, capsys):
    assert main(["deliver", "--client", "acme", "--out", ws.out(), "--env-file", str(ws.env_file)]) == 0
    assert ledger_summary(ws.pb_db)["company"] == 3
    assert not ws.db.exists() and not (ws.root / "data" / "leadgen.db").exists()


def test_deliver_says_when_db_is_not_the_client_playbooks_database(ws, capsys):
    # the playbook's own database (storage.path), named with --db: nothing to warn about
    rel = ws.pb_db.relative_to(ws.root)
    assert main(["deliver", "--client", "acme", "--out", ws.out(), "--db", str(rel),
                 "--env-file", str(ws.env_file)]) == 0
    out = capsys.readouterr().out
    assert "Next: send the files in" in out and "Careful" not in out
    # another database: the "Next:" line says whose history and do-not-lists were applied
    assert main(["deliver", "--client", "acme", "--out", str(ws.root / "other" / "{client}" / "{date}"),
                 *ws.g()]) == 0
    out = capsys.readouterr().out
    assert (f"Careful: this delivery used --db {ws.db}, not {ws.pb_db} (the database of this client's "
            f"playbook, storage.path)") in out
    assert "never the files of a rehearsal" in out


def test_readme_rehearsal_on_a_copy_of_the_database_applies_the_do_not_lists(ws, capsys):
    """README step 5: rehearse on a copy of the real database (its do-not-lists + history apply),
    into a scratch folder; the real ledger records nothing."""
    env = ["--env-file", str(ws.env_file)]
    assert main(["suppress", "add", "casey@acme-dlv.example", "--client", "acme", *env]) == 0   # real database
    assert main(["suppress", "add", "beta-dlv.example", "-p", str(ws.playbook_path), *env]) == 0
    rehearsal_db = ws.root / "data" / "rehearsal.db"
    rehearsal_db.write_bytes(ws.pb_db.read_bytes())                                          # cp data/... data/...
    capsys.readouterr()
    assert main(["deliver", "--client", "acme", "--db", str(rehearsal_db),
                 "--out", str(ws.root / "rehearsal" / "{client}" / "{date}"), *env]) == 0
    out = capsys.readouterr().out
    rows = read_csv(next((ws.root / "rehearsal" / "acme").glob(f"*/acme-hiring-signals-{TODAY}.csv")))
    assert "Beta LLC" not in {r["Company"] for r in rows}                   # the global do-not-list
    assert "casey@acme-dlv.example" not in {r["Email"] for r in rows}     # the client's do-not-list
    assert "Careful: this delivery used --db" in out and "never the files of a rehearsal" in out
    assert ledger_summary(ws.pb_db)["items"] == 0                         # the real ledger knows nothing


def test_deliver_ignores_p_with_a_warning(ws, capsys):
    assert deliver_cmd(ws, "-p", "playbooks/whatever.yaml") == 0
    assert "-p is ignored by 'deliver'" in capsys.readouterr().err


def test_deliver_client_without_contact_gets_a_tip(ws, capsys):
    ws.client(contact={})
    assert deliver_cmd(ws) == 0
    out = capsys.readouterr().out
    assert f"Next: send the files in {ws.folder()} to Acme Staffing Ltd (not the _internal folder" in out
    assert "tip: add 'contact: {name: ..., email: ...}'" in out


def test_deliver_with_clients_dir(ws, capsys):
    other = ws.root / "agencies"
    other.mkdir()
    (ws.root / "clients" / "acme.yaml").rename(other / "northwind.yaml")
    assert main(["deliver", "--client", "northwind", "--clients-dir", str(other), "--out", ws.out(), *ws.g()]) == 0
    assert (ws.folder("northwind") / f"northwind-hiring-signals-{TODAY}.csv").is_file()
    capsys.readouterr()
    assert main(["deliver", "--client", "northwind", "--out", ws.out(), *ws.g()]) == 2   # not in clients/


def test_without_console_summary_keeps_other_channels(ws):
    client = load_client("acme")
    ws.playbook(notify={"channels": [{"type": "console"}, {"type": "console", "stream": "stderr"},
                                     {"type": "webhook", "url": "https://hooks.example/x"}],
                        "on": ["run_summary"]})
    pb = client_playbook(client, env={})
    quiet = cli.without_console_summary(client, pb)
    assert quiet is not client and client.overrides == {}
    channels = client_playbook(quiet, env={}).notify["channels"]
    assert [c.get("type") for c in channels] == ["console", "webhook"]
    assert channels[0]["stream"] == "stderr"
    # nothing to drop: the same object comes back
    ws.playbook(notify={"channels": [{"type": "console"}], "on": []})
    assert cli.without_console_summary(client, client_playbook(client, env={})) is client


# =============================================================================
# clients
# =============================================================================

def test_clients_list_shows_volume_and_delivery_history(ws, capsys):
    ws.client("beta", display_name="Beta Recruiting", leads_per_week=25)
    for argv in (["clients"], ["clients", "list"]):
        assert main([*argv, *ws.g()]) == 0
        out = capsys.readouterr().out
        assert "Clients in clients/: 2" in out
        assert re.search(r"acme\s+Acme Staffing Ltd\s+10\s+0\s+never\s+0", out), out
        assert re.search(r"beta\s+Beta Recruiting\s+25\s+0\s+never\s+0", out), out
    assert not ws.db.exists()                  # listing never creates a database
    assert deliver_cmd(ws) == 0
    capsys.readouterr()
    assert main(["clients", *ws.g()]) == 0
    out = capsys.readouterr().out
    assert re.search(rf"acme\s+Acme Staffing Ltd\s+10\s+1\s+{TODAY}\s+3", out), out
    assert re.search(r"beta\s+Beta Recruiting\s+25\s+0\s+never\s+0", out), out
    assert "leadgen deliver --client <name> --dry-run" in out


def test_clients_list_footer_is_true_when_a_client_file_allows_redelivery(ws, capsys):
    assert main(["clients", *ws.g()]) == 0
    out = capsys.readouterr().out
    assert "No client file allows the same company, job or person to be delivered twice." in out
    # new jobs at known companies: Acme is delivered again, and counted once
    ws.client(dedupe=["job", "contact"])
    ws.client("beta", display_name="Beta Recruiting", redelivery_days=90)
    ws.client("gamma", display_name="Gamma Talent")
    assert deliver_cmd(ws) == 0
    ws.write_csv("jobs.csv", JOB_HEADER, job_rows() + [
        ["Acme Corp", "acme-dlv.example", "Austin, TX", "Software", 120, "job_posting", "Payroll Accountant",
         ago(1), "https://acme-dlv.example/jobs/3", "a-3"]])
    assert deliver_cmd(ws) == 0
    capsys.readouterr()
    assert main(["clients", *ws.g()]) == 0
    out = capsys.readouterr().out
    assert re.search(rf"acme\s+Acme Staffing Ltd\s+10\s+2\s+{TODAY}\s+3", out), out
    assert "total delivered = distinct companies sent so far (a company sent again is counted once)." in out
    assert "none of them is ever delivered" not in out
    assert re.search(r"May get the same company / job / person again \(.*\): acme, beta\. Every other", out), out


def test_clients_list_reads_each_clients_playbook_database(ws, capsys):
    assert main(["deliver", "--client", "acme", "--out", ws.out(), "--env-file", str(ws.env_file)]) == 0
    capsys.readouterr()
    assert main(["clients", "--env-file", str(ws.env_file)]) == 0
    assert re.search(rf"acme\s+Acme Staffing Ltd\s+10\s+1\s+{TODAY}\s+3", capsys.readouterr().out)


def test_clients_list_shows_history_of_a_deleted_client_file(ws, capsys):
    assert deliver_cmd(ws) == 0
    (ws.root / "clients" / "acme.yaml").unlink()
    capsys.readouterr()
    assert main(["clients", *ws.g()]) == 0
    out = capsys.readouterr().out
    assert re.search(rf"acme\s+\(no client file\)\s+-\s+1\s+{TODAY}\s+3", out), out


def test_clients_list_flags_an_invalid_client_file(ws, capsys):
    ws.client("broken", leads_per_week=0)
    assert main(["clients", *ws.g()]) == 1
    captured = capsys.readouterr()
    assert re.search(r"broken\s+\(invalid client file", captured.out)
    assert "acme" in captured.out
    assert "broken.yaml" in captured.err and "leads_per_week" in captured.err
    # a client whose base playbook is missing is flagged the same way
    ws.client("orphan", playbook=str(ws.root / "gone.yaml"))
    assert main(["clients", *ws.g()]) == 1
    captured = capsys.readouterr()
    assert re.search(r"orphan\s+\(invalid client file", captured.out)
    assert "base playbook" in captured.err and "gone.yaml" in captured.err


def test_clients_list_without_client_files(ws, capsys):
    assert main(["clients", "--clients-dir", str(ws.root / "none"), *ws.g()]) == 0
    out = capsys.readouterr().out
    assert "No client files in" in out and "does not exist yet" in out and "leadgen clients new <name>" in out


def test_clients_new_copies_the_template_and_prints_next_steps(ws, capsys):
    assert main(["clients", "new", "northwind-staffing", *ws.g()]) == 0
    out = capsys.readouterr().out
    path = ws.root / "clients" / "northwind-staffing.yaml"
    assert path.read_text(encoding="utf-8") == TEMPLATE.read_text(encoding="utf-8")
    assert f"Created {Path('clients') / 'northwind-staffing.yaml'}" in out
    for step in ("leadgen doctor --client northwind-staffing",
                 "leadgen deliver --client northwind-staffing --dry-run",
                 "leadgen deliver --client northwind-staffing "):
        assert step in out
    client = load_client("northwind-staffing")             # the copy is a valid client file
    assert client.name == "northwind-staffing" and client.leads_per_week == 25
    assert main(["clients", "new", "northwind-staffing.yaml", *ws.g()]) == 1     # never overwritten
    assert "already exists" in capsys.readouterr().err


def test_clients_new_bad_names_and_usage(ws, capsys):
    assert main(["clients", "new", "Bad Name", *ws.g()]) == 2
    assert "for example 'bad-name'" in capsys.readouterr().err
    assert main(["clients", "new", *ws.g()]) == 2
    assert "give the new client's short name" in capsys.readouterr().err
    assert main(["clients", "list", "acme", *ws.g()]) == 2
    assert "takes no name" in capsys.readouterr().err
    assert main(["clients", "delete", *ws.g()]) == 2        # argparse: invalid choice
    assert not (ws.root / "clients" / "bad name.yaml").exists()


def test_clients_new_in_another_folder(ws, capsys):
    folder = ws.root / "agencies"
    assert main(["clients", "new", "acme", "--clients-dir", str(folder), *ws.g()]) == 0
    out = capsys.readouterr().out
    assert (folder / "acme.yaml").is_file()
    assert f"leadgen deliver --client acme --clients-dir {folder} --dry-run" in out


# =============================================================================
# doctor
# =============================================================================

def doctor_workspace(ws: WS) -> None:
    ws.playbook(sources=[{"type": "adzuna", "countries": ["us"], "queries": ["accountant"]}],
                enrichment={"finders": [{"type": "apollo"}, {"type": "pattern"}],
                            "verifier": {"type": "millionverifier"}})
    ws.env(ADZUNA_APP_ID="adz-id-1", ADZUNA_APP_KEY="adz-key-secret", APOLLO_API_KEY="apollo-secret-key",
           MILLIONVERIFIER_API_KEY=MV_KEY)
    ws.http.add("GET", "https://api.adzuna.com/v1/api/jobs/us/search/1",
                json={"count": 1520, "mean": 61000.0, "results": [{"id": "1", "title": "Accountant"}]})
    ws.http.add("GET", "https://api.apollo.io/v1/auth/health", json={"healthy": True, "is_logged_in": True})


def test_doctor_playbook_checks_every_key_with_free_calls(ws, capsys):
    doctor_workspace(ws)
    ws.http.add("GET", "https://api.millionverifier.com/api/v3/credits", json={"credits": 9950})
    assert main(["doctor", "-p", str(ws.playbook_path), *ws.g()]) == 0
    out = capsys.readouterr().out
    assert "Checking the API keys of playbook 'cli-dlv-base'" in out and "delivery mode" in out
    assert re.search(r"status\s+adapter\s+detail\s+quota", out)
    assert re.search(r"ok\s+source adzuna\s+keys accepted \(1-result test search in US, 1,520 jobs listed\)", out)
    assert re.search(r"ok\s+finder apollo\s+key accepted", out)
    assert re.search(r"ok\s+verifier millionverifier\s+key accepted\s+9,950 credits left", out)
    assert re.search(r"skipped\s+finder pattern\s+no key needed", out)
    assert "3 ok, 0 failed, 0 missing key" in out and "Every key that was checked works" in out
    # one GET per key, to the free endpoints, keys never printed
    urls = sorted(c["url"] for c in ws.http.calls)
    assert urls == ["https://api.adzuna.com/v1/api/jobs/us/search/1", "https://api.apollo.io/v1/auth/health",
                    "https://api.millionverifier.com/api/v3/credits"]
    assert all(c["method"] == "GET" for c in ws.http.calls)
    apollo = next(c for c in ws.http.calls if "apollo" in c["url"])
    assert apollo["headers"]["x-api-key"] == "apollo-secret-key"
    for secret in ("adz-key-secret", "apollo-secret-key", MV_KEY):
        assert secret not in out
    assert not ws.db.exists()                  # the doctor never opens your database


def test_doctor_missing_and_rejected_keys_exit_1(ws, capsys):
    doctor_workspace(ws)
    ws.env(ADZUNA_APP_ID="adz-id-1", ADZUNA_APP_KEY="adz-key-secret", MILLIONVERIFIER_API_KEY=MV_KEY)
    ws.http.add("GET", "https://api.millionverifier.com/api/v3/credits", status=401,
                json={"error": "Invalid API key"})
    assert main(["doctor", "-p", str(ws.playbook_path), *ws.g()]) == 1
    captured = capsys.readouterr()
    out = captured.out
    assert re.search(r"MISSING KEY\s+finder apollo\s+missing key - set \$APOLLO_API_KEY", out)
    assert re.search(r"FAILED\s+verifier millionverifier\s+key rejected \(HTTP 401", out)
    assert "1 ok, 1 failed, 1 missing key" in out and "Fix the FAILED / MISSING KEY rows" in out
    assert "error: 2 adapter(s) need attention" in captured.err
    assert MV_KEY not in out


def test_doctor_client_checks_its_playbook_and_google_sheet_push(ws, capsys):
    ws.client(delivery={"google_sheet": {"spreadsheet_id": "sheet-123"}})
    assert main(["doctor", "--client", "acme", *ws.g()]) == 1
    out = capsys.readouterr().out
    assert "Checking the API keys of client 'acme' (Acme Staffing Ltd" in out
    assert re.search(r"skipped\s+source csv 'jobs'\s+no key needed \(file found", out)
    assert re.search(r"MISSING KEY\s+delivery google_sheet 'Google Sheet push'\s+missing key - set "
                     r"\$GOOGLE_APPLICATION_CREDENTIALS", out)
    # without a sheet push the client has nothing needing a key
    ws.client()
    assert main(["doctor", "--client", "acme", *ws.g()]) == 0
    out = capsys.readouterr().out
    assert "google_sheet" not in out and "Nothing here needs a key. Next: leadgen deliver --client acme --dry-run" in out


def test_doctor_dry_run_contacts_nobody(ws, capsys):
    doctor_workspace(ws)
    assert main(["doctor", "-p", str(ws.playbook_path), "--dry-run", *ws.g()]) == 0
    out = capsys.readouterr().out
    assert ws.http.calls == []
    assert "keys are only checked for presence" in out and "not contacted" in out


def test_doctor_needs_exactly_one_target(ws, capsys):
    assert main(["doctor", *ws.g()]) == 2
    assert "doctor needs -p PLAYBOOK or --client NAME" in capsys.readouterr().err
    assert main(["doctor", "-p", str(ws.playbook_path), "--client", "acme", *ws.g()]) == 2
    assert "not both" in capsys.readouterr().err
    assert main(["doctor", "--client", "nobody", *ws.g()]) == 2
    assert "client 'nobody' not found" in capsys.readouterr().err
    assert ws.http.calls == []


def test_doctor_flags_risky_scrapers(ws, capsys):
    ws.playbook(sources=[{"type": "linkedin_jobs", "search_urls": ["https://www.linkedin.com/jobs/search/?k=x"]}])
    assert main(["doctor", "-p", str(ws.playbook_path), *ws.g()]) == 1
    assert "use at own risk" in capsys.readouterr().out


# =============================================================================
# suppress --client (and the new kinds)
# =============================================================================

def test_suppress_client_round_trip(ws, capsys):
    c = ["--client", "acme", *ws.g()]
    assert main(["suppress", "list", *c]) == 0
    out = capsys.readouterr().out
    assert "The do-not-list of client 'acme'" in out and "is empty" in out
    assert main(["suppress", "add", "Jane@Partner-Demo.com", "--reason", "their candidate", *c]) == 0
    assert "Added 1 value(s) to the do-not-list of client 'acme'" in capsys.readouterr().out
    assert main(["suppress", "add", "partner-demo.com", *c]) == 0
    assert main(["suppress", "add", "https://www.linkedin.com/in/Jane-Doe/?trk=abc", *c]) == 0
    assert main(["suppress", "add", "Their Existing Client Inc", "--kind", "company", *c]) == 0
    capsys.readouterr()
    # a company name is never guessed
    assert main(["suppress", "add", "Another Client LLC", *c]) == 1
    captured = capsys.readouterr()
    assert "skipped 'Another Client LLC'" in captured.err and "--kind company" in captured.err

    assert main(["suppress", "list", *c]) == 0
    out = capsys.readouterr().out
    assert "4 value(s) on the do-not-list of client 'acme'" in out
    for value, kind in (("jane@partner-demo.com", "email"), ("partner-demo.com", "domain"),
                        ("linkedin.com/in/jane-doe", "linkedin"), ("their existing client", "company")):
        assert re.search(rf"{re.escape(value)}\s+{kind}\b", out), (value, out)
    assert "their candidate" in out

    store = Store(str(ws.db))
    ledger = Ledger(store)
    assert ledger.is_suppressed("acme", email="bob@partner-demo.com")
    assert ledger.is_suppressed("acme", linkedin="https://linkedin.com/in/jane-doe")
    assert ledger.is_suppressed("acme", company="Their Existing Client, Inc.")
    assert not ledger.is_suppressed("other", company="Their Existing Client")     # per client
    assert store.list_suppressed() == []                                          # not the global list
    store.close()

    # removing the email leaves the whole-domain entry alone
    assert main(["suppress", "remove", "jane@partner-demo.com", *c]) == 0
    assert "Removed 1 value(s) from the do-not-list of client 'acme'" in capsys.readouterr().out
    assert main(["suppress", "remove", "Their Existing Client Inc", *c]) == 0     # company without --kind
    assert main(["suppress", "remove", "https://linkedin.com/in/jane-doe", *c]) == 0
    assert main(["suppress", "remove", "never-added-demo.com", *c]) == 0
    assert "Removed 0 value(s) (of 1 given)" in capsys.readouterr().out
    store = Store(str(ws.db))
    assert [(r["value"], r["kind"]) for r in Ledger(store).list_suppressed("acme")] == [("partner-demo.com", "domain")]
    store.close()
    assert main(["suppress", "list", "--db", str(ws.db), "--env-file", str(ws.env_file)]) == 0
    assert "is empty" in capsys.readouterr().out                                  # global list untouched


def test_suppress_client_list_mentions_the_client_file_exclusions(ws, capsys):
    ws.client(exclusions={"companies": ["Their Client Inc"], "domains": ["bigclient-demo.com"],
                          "keywords": ["staffing"]})
    assert main(["suppress", "list", "--client", "acme", *ws.g()]) == 0
    out = capsys.readouterr().out
    assert "is empty" in out
    assert "'exclusions': companies: Their Client Inc; domains: bigclient-demo.com; keywords: staffing" in out


def test_suppress_client_blocks_the_next_delivery(ws, capsys):
    assert main(["suppress", "add", "gamma-dlv.example", "--client", "acme", *ws.g()]) == 0
    assert main(["suppress", "add", "Beta LLC", "--kind", "company", "--client", "acme", *ws.g()]) == 0
    capsys.readouterr()
    assert deliver_cmd(ws) == 0
    out = capsys.readouterr().out
    rows = read_csv(ws.folder() / f"acme-hiring-signals-{TODAY}.csv")
    assert len(rows) == 1 and "Acme Corp" in rows[0].values()
    assert "on a do-not-list ........... 2" in out


def test_suppress_client_without_db_uses_the_client_playbooks_database(ws, capsys):
    env = ["--env-file", str(ws.env_file)]
    assert main(["suppress", "add", "gamma-dlv.example", "--client", "acme", *env]) == 0
    assert f"in {ws.pb_db}" in capsys.readouterr().out
    assert not (ws.root / "data" / "leadgen.db").exists()
    assert main(["deliver", "--client", "acme", "--out", ws.out(), *env]) == 0
    rows = read_csv(ws.folder() / f"acme-hiring-signals-{TODAY}.csv")
    assert "Gamma Inc" not in {r for row in rows for r in row.values()}


def test_suppress_client_unknown_client(ws, capsys):
    assert main(["suppress", "add", "x@y-demo.com", "--client", "acmee", *ws.g()]) == 2
    err = capsys.readouterr().err
    assert "client 'acmee' not found" in err and "did you mean 'acme'?" in err and "clients new acmee" in err
    assert not ws.db.exists()
    # listing / removing for a client whose file is gone still works (with --db)
    assert main(["suppress", "list", "--client", "gone", *ws.g()]) == 0
    assert "The do-not-list of client 'gone'" in capsys.readouterr().out


def _case_insensitive_disk(monkeypatch) -> None:
    """Make file lookups ignore case, like the default macOS (APFS) and Windows (NTFS) disks:
    'clients/ACME.yaml' opens 'clients/acme.yaml'. Directory listings keep the stored names."""
    import os

    real_is_file, real_exists, real_read = Path.is_file, Path.exists, Path.read_text

    def on_disk(p: Path) -> Path:
        try:
            names = os.listdir(p.parent)
        except OSError:
            return p
        same = [n for n in names if n.casefold() == p.name.casefold()]
        return p.with_name(same[0]) if p.name not in names and len(same) == 1 else p

    monkeypatch.setattr(Path, "is_file", lambda self: real_is_file(on_disk(self)))
    monkeypatch.setattr(Path, "exists", lambda self: real_exists(on_disk(self)))
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: real_read(on_disk(self), *a, **k))


def test_client_name_typed_in_another_case_is_the_same_client(ws, capsys, monkeypatch):
    """On a case-insensitive disk `--client ACME` opens clients/acme.yaml: it must be client
    'acme' - its ledger and do-not-list - not a new client 'ACME' with an empty history."""
    _case_insensitive_disk(monkeypatch)
    assert deliver_cmd(ws) == 0
    capsys.readouterr()
    assert main(["suppress", "add", "casey@acme-dlv.example", "--client", "ACME", *ws.g()]) == 0
    assert "do-not-list of client 'acme'" in capsys.readouterr().out
    assert main(["suppress", "list", "--client", "Acme", *ws.g()]) == 0
    assert "casey@acme-dlv.example" in capsys.readouterr().out
    assert main(["deliver", "--client", "ACME", "--out", ws.out(), *ws.g()]) == 1
    out = capsys.readouterr().out
    assert "Delivering the Hiring Signal Report for Acme Staffing Ltd (acme)" in out
    assert "delivered .................. 0 (target 10)" in out           # nothing twice
    store = Store(str(ws.db))
    try:
        ledger = Ledger(store)
        assert ledger.list_clients_with_history() == ["acme"]
        assert [r["value"] for r in ledger.list_suppressed("acme")] == ["casey@acme-dlv.example"]
    finally:
        store.close()


def test_suppress_client_from_a_company_csv(ws, capsys):
    f = ws.root / "their-clients.csv"
    f.write_text("First name,Last name,Company,Email\nJo,Doe,Acme Corp,jo@acme-dlv.example\n"
                 "Al,Roe,Beta LLC,al@beta-dlv.example\n", encoding="utf-8")
    assert main(["suppress", "add", "--file", str(f), "--kind", "company", "--client", "acme", *ws.g()]) == 0
    assert "Added 2 value(s)" in capsys.readouterr().out
    store = Store(str(ws.db))
    assert sorted((r["value"], r["kind"]) for r in Ledger(store).list_suppressed("acme")) == [
        ("acme", "company"), ("beta", "company")]
    store.close()


def test_suppress_global_list_accepts_company_and_linkedin(ws, capsys):
    g = ws.g()
    assert main(["suppress", "add", "https://www.linkedin.com/in/Jane-Doe/?trk=x", *g]) == 0
    assert main(["suppress", "add", "The Acme Holdings Inc", "--kind", "company", *g]) == 0
    assert main(["suppress", "add", "linkedin.com/in/bob", "--kind", "linkedin", *g]) == 0
    capsys.readouterr()
    assert main(["suppress", "add", "The Acme Holdings Inc", *g]) == 1
    assert "for a company name add --kind company" in capsys.readouterr().err
    assert main(["suppress", "add", "not a profile", "--kind", "linkedin", *g]) == 1
    capsys.readouterr()
    store = Store(str(ws.db))
    assert sorted((r["value"], r["kind"]) for r in store.list_suppressed()) == [
        ("acme", "company"), ("linkedin.com/in/bob", "linkedin"), ("linkedin.com/in/jane-doe", "linkedin")]
    assert store.is_suppressed(company="Acme Holdings") and store.is_suppressed(linkedin="linkedin.com/in/jane-doe/")
    assert main(["suppress", "remove", "Acme Holdings", *g]) == 0          # company name without --kind
    assert main(["suppress", "remove", "https://linkedin.com/in/jane-doe", *g]) == 0
    assert "Removed 1 value(s)" in capsys.readouterr().out
    assert [(r["value"], r["kind"]) for r in store.list_suppressed()] == [("linkedin.com/in/bob", "linkedin")]
    store.close()


@pytest.mark.parametrize("raw, kind, expected", [
    ("Jane@Acme-Demo.com", None, ("jane@acme-demo.com", "email")),
    ("https://www.acme-demo.com/about", None, ("acme-demo.com", "domain")),
    ("*@acme-demo.com", None, ("acme-demo.com", "domain")),
    ("https://www.linkedin.com/in/jane-doe/", None, ("https://www.linkedin.com/in/jane-doe/", "linkedin")),
    ("uk.linkedin.com/in/jane", None, ("uk.linkedin.com/in/jane", "linkedin")),
    ("Acme Corp", None, None),
    ("Acme Corp", "company", ("Acme Corp", "company")),
    ("Inc.", "company", None),
    ("jane-doe", "linkedin", None),
    ("acme-demo.com", "email", None),
    ("", None, None),
])
def test_suppression_value_kinds(raw, kind, expected):
    assert suppression_value(raw, kind) == expected


# =============================================================================
# replies / serve / followups: outbound mode only
# =============================================================================

GATED = [["replies", "--file", "replies.csv"], ["serve", "--port", "0"], ["followups"],
         ["followups", "--done", "1"]]


@pytest.mark.parametrize("argv", GATED, ids=lambda a: " ".join(a))
def test_outbound_commands_refuse_a_delivery_playbook(ws, capsys, monkeypatch, argv):
    def never(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("nothing may be opened in delivery mode")

    monkeypatch.setattr(cli, "open_session", never)
    monkeypatch.setattr(cli, "open_store", never)
    assert main([*argv, "-p", str(ws.playbook_path), *ws.g()]) == 2
    captured = capsys.readouterr()
    assert "is an outbound-mode feature, and playbook 'cli-dlv-base' runs in delivery mode" in captured.err
    assert "Add 'mode: outbound' to the playbook" in captured.err
    assert captured.out == ""
    assert not ws.db.exists() and not (ws.root / "output").exists()


@pytest.mark.parametrize("argv", GATED, ids=lambda a: " ".join(a))
def test_outbound_commands_without_a_playbook_default_to_delivery(ws, capsys, argv):
    assert main([*argv, *ws.g()]) == 2
    err = capsys.readouterr().err
    assert "is an outbound-mode feature" in err
    assert "No playbook was given (-p), so the default mode (delivery) applies" in err
    assert f"leadgen {argv[0]} -p playbooks/demo-offline.yaml" in err
    assert not ws.db.exists()


def test_outbound_commands_still_run_with_an_outbound_playbook(ws, capsys):
    ws.playbook(mode="outbound")
    assert main(["followups", "-p", str(ws.playbook_path), *ws.g()]) == 0
    assert "No follow-ups due today" in capsys.readouterr().out
    empty = ws.root / "replies.csv"
    empty.write_text("from,body\n", encoding="utf-8")
    assert main(["replies", "-p", str(ws.playbook_path), "--file", str(empty), *ws.g()]) == 1
    assert "No replies found" in capsys.readouterr().out     # past the gate: the file was read


# =============================================================================
# run --budget + usage lines
# =============================================================================

def run_cmd(ws: WS, *extra: str) -> int:
    return main(["run", "-p", str(ws.playbook_path), "--out", str(ws.root / "runs"), *ws.g(), *extra])


@pytest.mark.parametrize("flag, calls, shown", [
    (None, 2, "paid lookups 2/2"),            # no flag: the playbook's own usage.max_paid_lookups (2)
    ("1", 1, "paid lookups 1/1"),
    ("3", 3, "paid lookups 3/3"),             # the flag overrides the playbook, also upwards
    ("0", 4, "paid lookups 4 (no cap)"),      # 0 = no cap
])
def test_run_budget_flag_caps_paid_lookups_and_prints_usage(ws, capsys, flag, calls, shown):
    mv_workspace(ws, usage={"max_paid_lookups": 2, "cost_per_call": {"millionverifier": 0.004}})
    assert run_cmd(ws, *(["--budget", flag] if flag is not None else [])) == 0
    out = capsys.readouterr().out
    mv = [c for c in ws.http.calls if c["url"].startswith(MV_URL)]
    assert len(mv) == len(ws.http.calls) == calls
    assert all(c["params"]["api"] == MV_KEY for c in mv) and MV_KEY not in out
    assert shown in out and out.count("API usage:") == 1
    assert ("warning: paid-lookup budget" in out) == (calls < 4)       # the run summary says so
    assert (f"at most {flag} paid lookup(s)" in out) == (flag not in (None, "0"))
    assert f"estimated cost: ~${calls * 0.004:.4f}" in out


def test_run_prints_usage_with_and_without_a_console_notifier(ws, capsys):
    assert run_cmd(ws) == 0
    out = capsys.readouterr().out
    assert "[RUN SUMMARY]" in out and out.count("API usage: paid lookups 0 (no cap)") == 1
    ws.playbook(notify={"channels": [], "on": []})
    assert run_cmd(ws) == 0
    out = capsys.readouterr().out
    assert "[RUN SUMMARY]" not in out and out.count("API usage: paid lookups 0 (no cap)") == 1
    assert "delivery mode" in out and "leadgen deliver --client <name>" in out


PAID_PLUGIN = """
from leadgen import registry
from leadgen.models import Company, Signal
from leadgen.sources.base import Source


class PaidJobs(Source):
    \"\"\"A job API that charges per request (README: register it with paid=True).\"\"\"

    name = "paid_jobs"
    env_key = "PAID_JOBS_KEY"

    def fetch(self):
        out = []
        for page in (1, 2, 3):
            data = self.http.get_json(f"https://jobs-api.example/search/{page}", params={"key": self.secret()})
            out += [Company(name=j["company"], domain=j["domain"], sources=[self.label],
                            signals=[Signal(type="job_posting", title=j["title"], source=self.label)])
                    for j in data.get("jobs", [])]
        return out


registry.register("source", "paid_jobs", "cli_paid_plugin:PaidJobs", paid=True)
"""


def test_plugin_registered_as_paid_is_capped_by_budget(ws, capsys, monkeypatch):
    """README 'Extending': registry.register(..., paid=True) makes a plugin's requests paid
    lookups - shown as paid, counted, and stopped by --budget before they reach the network."""
    from leadgen import registry

    (ws.root / "cli_paid_plugin.py").write_text(PAID_PLUGIN, encoding="utf-8")
    monkeypatch.syspath_prepend(str(ws.root))
    ws.env(LEADGEN_PLUGINS="cli_paid_plugin", PAID_JOBS_KEY="k-123")
    ws.http.add("GET", re.compile(r"^https://jobs-api\.example/search/\d$"), json={"jobs": []})
    try:
        assert main(["adapters", *ws.g()]) == 0
        assert "network (paid)" in _line(capsys.readouterr().out, "paid_jobs")
        ws.playbook(sources=[{"type": "paid_jobs"}])
        assert run_cmd(ws, "--budget", "1") == 0
        out = capsys.readouterr().out
        assert len(ws.http.calls) == 1                      # the 2nd request never reached the network
        assert "paid lookups 1/1" in out and "k-123" not in out
    finally:
        registry._REGISTRY["source"].pop("paid_jobs", None)
        registry.register("source", "paid_jobs", "cli_paid_plugin:PaidJobs", paid=False)
        registry._REGISTRY["source"].pop("paid_jobs", None)


def test_run_negative_budget_is_a_usage_error(ws, capsys):
    assert run_cmd(ws, "--budget", "-5") == 2
    assert "--budget must be 0 or more" in capsys.readouterr().err
    assert not ws.db.exists()


# =============================================================================
# adapters / validate / demo / help
# =============================================================================

def _line(out: str, name: str) -> str:
    return next(ln for ln in out.splitlines() if re.match(rf"\s*{re.escape(name)}\s", ln))


def test_adapters_marks_paid_types_and_risky_scrapers(ws, capsys):
    assert main(["adapters", *ws.g()]) == 0
    out = capsys.readouterr().out
    assert re.search(r"type\s+runs\s+credential\s+description\s+notes", out)
    assert "use at own risk: scrapes LinkedIn via Apify" in _line(out, "linkedin_jobs")
    assert "network (paid)" in _line(out, "linkedin_jobs") and "network (paid)" in _line(out, "theirstack")
    assert "network (paid)" in _line(out, "millionverifier") and "network (paid)" in _line(out, "anthropic")
    adzuna = _line(out, "adzuna")
    assert " network " in adzuna and "paid" not in adzuna
    assert "use-at-own-risk" in _line(out, "apify")
    assert "outbound mode only" in _line(out, "instantly_csv")
    assert "outbound mode only" in _line(out, "template")
    assert "outbound mode only" not in _line(out, "gsheets")
    assert "ADZUNA_APP_ID, ADZUNA_APP_KEY" in out


def mixed_playbook(ws: WS, mode: str) -> str:
    data = {
        "name": "mixed", "mode": mode,
        "sources": [{"type": "csv", "path": str(ws.base / "jobs.csv")},
                    {"type": "linkedin_jobs", "api_key": "apify-test-token",
                     "search_urls": ["https://www.linkedin.com/jobs/search/?keywords=accountant"]}],
        "writer": {"type": "ai", "provider": "anthropic", "model": "claude-sonnet-5"},
        "outbound": {"exporters": [{"type": "instantly"}, {"type": "smartlead_csv"}, {"type": "csv"}]},
        "buyers": {"titles": ["CFO"]},
        "delivery": {"sender_name": "Alex"},
    }
    path = ws.root / f"mixed-{mode}.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return str(path)


def test_validate_delivery_mode_warns_about_outbound_only_config(ws, capsys):
    assert main(["validate", "-p", mixed_playbook(ws, "delivery"), *ws.g()]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[1].startswith("Mode: delivery - lead files only")
    assert "[FAIL]" not in out
    assert "[warn] writer ai (anthropic, claude-sonnet-5) - ignored in delivery mode" in out
    assert re.search(r"\[warn\] AI opening lines \(anthropic, claude-sonnet-5\) - .*\$ANTHROPIC_API_KEY.*"
                     r"only matters for clients with opening_line.ai: true", out)
    assert "[warn] exporter instantly - hands leads to a sending tool - ignored in delivery mode" in out
    assert "[warn] exporter smartlead_csv - hands leads to a sending tool" in out
    assert "missing 'campaign_id'" not in out and "$INSTANTLY_API_KEY" not in out
    assert "[ok]   exporter csv" in out
    assert "[warn] source linkedin_jobs - use at own risk: scrapes LinkedIn via Apify" in out
    assert "offer.sender_name" not in out
    assert "OK - ready to run" in out and "leadgen deliver --client <name> --dry-run" in out


def test_validate_outbound_mode_keeps_checking_hand_over_config(ws, capsys):
    assert main(["validate", "-p", mixed_playbook(ws, "outbound"), *ws.g()]) == 1
    out = capsys.readouterr().out
    assert out.splitlines()[1].startswith("Mode: outbound")
    assert "[FAIL] exporter instantly - missing 'campaign_id'" in out and "$INSTANTLY_API_KEY" in out
    assert "[FAIL] writer ai (anthropic, claude-sonnet-5)" in out
    assert "use at own risk" in out and "offer.sender_name is empty" in out
    assert "ignored in delivery mode" not in out


def test_validate_delivery_mode_key_problems_of_the_ai_model_are_warnings(ws, capsys, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    path = ws.root / "compat.yaml"
    path.write_text(yaml.safe_dump({
        "name": "compat", "sources": [{"type": "csv", "path": str(ws.base / "jobs.csv")}],
        "writer": {"type": "template", "provider": "openai_compatible", "model": "llama",
                   "base_url": "https://openrouter.ai/api/v1"}}), encoding="utf-8")
    assert main(["validate", "-p", str(path), *ws.g()]) == 0
    out = capsys.readouterr().out
    assert "[skip] writer - not used in delivery mode" in out
    assert "[warn] AI opening lines (openai_compatible, llama)" in out and "api_key_env" in out
    assert "[warn] delivery - delivery.sender_name is empty" in out


def test_validate_every_delivery_playbook_shows_its_mode(ws, capsys, monkeypatch):
    monkeypatch.chdir(REPO)
    for name, mode in (("demo-delivery.yaml", "delivery"), ("recruitment-delivery.yaml", "delivery"),
                       ("demo-offline.yaml", "outbound")):
        main(["validate", "-p", f"playbooks/{name}", *ws.g()])
        out = capsys.readouterr().out
        assert out.splitlines()[1].startswith(f"Mode: {mode}"), name


def test_demo_report_after_a_delivery_mode_run(ws, capsys):
    assert run_cmd(ws) == 0
    capsys.readouterr()
    demo_dir = ws.root / "demo"
    assert main(["demo", "-p", str(ws.playbook_path), "--top", "3", "--prospect", "Acme Staffing",
                 "--out", str(demo_dir), *ws.g()]) == 0
    md = (demo_dir / "demo-acme-staffing.md").read_text(encoding="utf-8")
    page = (demo_dir / "demo-acme-staffing.html").read_text(encoding="utf-8")
    assert "Acme Corp" in md and "Acme Corp" in page
    assert "Suggested first email" not in md and "Suggested first email" not in page   # no copy in delivery mode


def test_demo_report_never_calls_a_guessed_email_verified(ws, capsys):
    # a paid checker that says "ok" to every address; Gamma's controller has no address in the
    # contact list, so the pattern finder guesses one - the client file says guessed-unverified
    mv_workspace(ws)
    assert run_cmd(ws) == 0
    capsys.readouterr()
    demo_dir = ws.root / "demo"
    for mask in ([], ["--no-mask"]):
        assert main(["demo", "-p", str(ws.playbook_path), "--top", "0", "--out", str(demo_dir), *mask,
                     *ws.g()]) == 0
        md = (demo_dir / "demo.md").read_text(encoding="utf-8")
        page = (demo_dir / "demo.html").read_text(encoding="utf-8")
        gamma = next(ln for ln in md.splitlines() if "@gamma-dlv.example" in ln)
        assert "Pat Numbers" in gamma and gamma.endswith("(guessed-unverified)"), gamma
        assert "guessed-unverified" in page
        # Acme / Beta / Zeta come from the list and the checker confirmed them: 3 verified, not 4
        assert "decision-maker at **4** of them (3 with a verified email)" in md
        assert md.count("(verified)") == 3
    # the stored leads are unchanged (only the one-pager's copy is relabelled)
    store = Store(str(ws.db))
    try:
        stored = [ld for ld in store.list_leads(None, limit=100) if ld.contact and "gamma" in ld.contact.email]
    finally:
        store.close()
    assert stored and all(ld.contact.email_status == "valid" for ld in stored)


def test_demo_report_still_works_for_an_outbound_run(ws, capsys, monkeypatch):
    monkeypatch.chdir(REPO)
    assert main(["run", "-p", "playbooks/demo-offline.yaml", "--out", str(ws.root / "runs"), *ws.g()]) == 0
    capsys.readouterr()
    assert main(["demo", "-p", "playbooks/demo-offline.yaml", "--out", str(ws.root / "demo"), *ws.g()]) == 0
    assert "Suggested first email" in (ws.root / "demo" / "demo.md").read_text(encoding="utf-8")


def test_help_lists_the_delivery_commands(ws, capsys):
    assert main(["--help"]) == 0
    out = capsys.readouterr().out
    for command in ("deliver", "clients", "doctor", "suppress", "adapters"):
        assert command in out
    assert main(["deliver", "--help"]) == 0
    out = capsys.readouterr().out
    assert "--client" in out and "--budget" in out and "--dry-run" in out and "--clients-dir" in out


def test_check_budget():
    assert cli.check_budget(None) is None and cli.check_budget(0) == 0 and cli.check_budget(7) == 7
    with pytest.raises(cli.CliError, match="0 or more"):
        cli.check_budget(-1)
