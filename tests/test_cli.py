"""CLI tests: every command through ``main([...])`` with a temporary database and output folder."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import List

import pytest
import yaml

from leadgen import cli
from leadgen.cli import main, parse_env_text
from leadgen.models import Stage
from leadgen.playbook import load_playbook
from leadgen.store import Store
from tests.conftest import SHIPPED_PLAYBOOKS

REPO = Path(__file__).resolve().parent.parent
DEMO = "playbooks/demo-offline.yaml"

# Every env var an adapter or playbook in this repo reads - cleared so a developer's
# real keys never change what these tests see.
_ENV_VARS = ("THEIRSTACK_API_KEY", "ADZUNA_APP_ID", "ADZUNA_APP_KEY", "APOLLO_API_KEY", "APIFY_TOKEN",
             "HUNTER_API_KEY", "MILLIONVERIFIER_API_KEY", "ZEROBOUNCE_API_KEY", "NEVERBOUNCE_API_KEY",
             "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "INSTANTLY_API_KEY", "SMARTLEAD_API_KEY",
             "SLACK_WEBHOOK_URL", "LEADGEN_WEBHOOK_URL", "LEADGEN_EXPORT_WEBHOOK_URL",
             "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_SERVICE_ACCOUNT_JSON", "LEADGEN_WEBHOOK_TOKEN",
             "SENDER_NAME", "SENDER_TITLE", "SENDER_COMPANY", "SENDER_WEBSITE", "BOOKING_LINK",
             "INSTANTLY_CAMPAIGN_ID", "SMARTLEAD_CAMPAIGN_ID", "LEADGEN_PLUGINS")


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """Run from the repo root (playbook paths are relative to it) with a clean env."""
    monkeypatch.chdir(REPO)
    for k in _ENV_VARS:
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def env_file(tmp_path):
    p = tmp_path / "test.env"
    p.write_text("# empty on purpose\n", encoding="utf-8")
    return str(p)


@pytest.fixture
def g(tmp_path, env_file):
    """Global options for an isolated run: tmp db + tmp env file."""
    return ["--db", str(tmp_path / "t.db"), "--env-file", env_file]


def write_playbook(tmp_path: Path, data: dict, name: str = "pb.yaml") -> str:
    p = tmp_path / name
    p.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return str(p)


def run_demo(g: List[str], tmp_path: Path, *extra: str) -> int:
    return main(["run", "-p", DEMO, "--out", str(tmp_path / "out"), *g, *extra])


def latest_run_dir(tmp_path: Path) -> Path:
    runs = sorted((tmp_path / "out").iterdir())
    return runs[-1]


# =============================================================================
# .env parsing
# =============================================================================

def test_parse_env_text_formats():
    text = "\n".join([
        "# comment",
        "",
        "PLAIN=abc",
        "export EXPORTED=1",
        "SPACED = hello world  ",
        "INLINE=value # a comment",
        "HASH_IN_VALUE=a#b",
        "SINGLE='it''s # not a comment'",
        'DOUBLE="line1\\nline2 \\"quoted\\""',
        "EMPTY=",
        "COMMENT_ONLY=   # nothing here",
        "not a valid line",
        "1BAD=x",
        'OPEN="unterminated',
    ])
    values, warnings = parse_env_text(text)
    assert values["PLAIN"] == "abc"
    assert values["EXPORTED"] == "1"
    assert values["SPACED"] == "hello world"
    assert values["INLINE"] == "value"
    assert values["HASH_IN_VALUE"] == "a#b"
    assert values["SINGLE"] == "it"          # first closing quote ends a single-quoted value
    assert values["DOUBLE"] == 'line1\nline2 "quoted"'
    assert values["EMPTY"] == "" and values["COMMENT_ONLY"] == ""
    assert "1BAD" not in values and "OPEN" not in values
    assert len(warnings) == 3 and any("line 12" in w for w in warnings)


def test_env_example_parses_cleanly():
    values, warnings = parse_env_text((REPO / ".env.example").read_text(encoding="utf-8"))
    assert warnings == []
    assert values["APOLLO_API_KEY"] == "" and values["BOOKING_LINK"] == ""   # "KEY=   # comment" is empty
    assert values["SENDER_NAME"] == "Your Name"
    for key in ("THEIRSTACK_API_KEY", "ADZUNA_APP_ID", "ADZUNA_APP_KEY", "APIFY_TOKEN", "HUNTER_API_KEY",
                "MILLIONVERIFIER_API_KEY", "ZEROBOUNCE_API_KEY", "NEVERBOUNCE_API_KEY", "ANTHROPIC_API_KEY",
                "OPENAI_API_KEY", "INSTANTLY_API_KEY", "SMARTLEAD_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS",
                "GOOGLE_SERVICE_ACCOUNT_JSON", "LEADGEN_EXPORT_WEBHOOK_URL", "SLACK_WEBHOOK_URL",
                "LEADGEN_WEBHOOK_URL", "LEADGEN_WEBHOOK_TOKEN"):
        assert key in values, key


def test_load_env_file_never_overrides_existing(tmp_path):
    p = tmp_path / ".env"
    p.write_text("A=from-file\nB=from-file\n", encoding="utf-8")
    env = {"A": "from-env"}
    added = cli.load_env_file(p, env)
    assert env == {"A": "from-env", "B": "from-file"} and added == ["B"]
    assert cli.load_env_file(tmp_path / "missing.env", env) == []
    with pytest.raises(FileNotFoundError):
        cli.load_env_file(tmp_path / "missing.env", env, required=True)


def test_env_file_feeds_playbook_placeholders(tmp_path, g, capsys, monkeypatch):
    # outbound: offer.sender_name signs emails (delivery mode never writes any, so never warns about it)
    pb = write_playbook(tmp_path, {"name": "envtest", "mode": "outbound", "offer": {"sender_name": "${SENDER_NAME}"},
                                   "sources": [{"type": "csv", "path": "examples/data/demo_signals.csv"}],
                                   "buyers": {"titles": ["CEO"]}})
    assert main(["validate", "-p", pb, *g]) == 0
    assert "offer.sender_name is empty" in capsys.readouterr().out
    envf = tmp_path / "with-name.env"
    envf.write_text("SENDER_NAME=Alex Morgan\n", encoding="utf-8")
    assert main(["validate", "-p", pb, "--db", str(tmp_path / "t.db"), "--env-file", str(envf)]) == 0
    assert "offer.sender_name is empty" not in capsys.readouterr().out
    # an already-set environment variable wins over the file
    monkeypatch.setenv("SENDER_NAME", "")
    assert main(["validate", "-p", pb, "--db", str(tmp_path / "t.db"), "--env-file", str(envf)]) == 0
    assert "offer.sender_name is empty" in capsys.readouterr().out


def test_explicit_missing_env_file_is_a_friendly_error(tmp_path, capsys):
    code = main(["validate", "-p", DEMO, "--db", str(tmp_path / "t.db"), "--env-file", str(tmp_path / "nope.env")])
    err = capsys.readouterr().err
    assert code == 2 and err.startswith("error: env file not found") and "Traceback" not in err


# =============================================================================
# parser basics + errors
# =============================================================================

def test_no_command_prints_help(capsys):
    assert main([]) == 2
    assert "usage: leadgen" in capsys.readouterr().out


def test_version_and_bad_arguments(capsys):
    assert main(["--version"]) == 0
    assert "leadgen" in capsys.readouterr().out
    assert main(["run", "--limit", "abc", "-p", DEMO]) == 2
    assert main(["nonsense"]) == 2


def test_global_options_before_or_after_the_command(tmp_path, env_file, capsys):
    db = str(tmp_path / "t.db")
    assert main(["-p", DEMO, "--db", db, "--env-file", env_file, "validate"]) == 0
    before = capsys.readouterr().out
    assert main(["validate", "-p", DEMO, "--db", db, "--env-file", env_file]) == 0
    assert capsys.readouterr().out == before


def test_command_that_needs_a_playbook(g, capsys):
    assert main(["run", *g]) == 2
    err = capsys.readouterr().err
    assert "needs a playbook" in err and "-p playbooks/" in err


def test_missing_and_invalid_playbooks_are_friendly(tmp_path, g, capsys):
    assert main(["validate", "-p", str(tmp_path / "nope.yaml"), *g]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: playbook not found") and "Traceback" not in err
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: [unclosed\n", encoding="utf-8")
    assert main(["validate", "-p", str(bad), *g]) == 2
    assert "could not parse" in capsys.readouterr().err
    invalid = write_playbook(tmp_path, {"name": "bad name!"}, "invalid.yaml")
    assert main(["validate", "-p", invalid, *g]) == 2
    assert "'name' may only contain" in capsys.readouterr().err


def test_unexpected_error_is_one_line_unless_verbose(monkeypatch, g, capsys):
    def boom(args):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(cli, "cmd_adapters", boom)
    assert main(["adapters", *g]) == 1
    err = capsys.readouterr().err
    assert "error: unexpected RuntimeError: kaboom" in err and "Traceback" not in err
    assert main(["adapters", "-v", *g]) == 1
    assert "Traceback" in capsys.readouterr().err


def test_friendly_error_shows_traceback_only_with_verbose(tmp_path, g, capsys):
    assert main(["validate", "-p", str(tmp_path / "nope.yaml"), *g]) == 2
    assert "Traceback" not in capsys.readouterr().err
    assert main(["validate", "-v", "-p", str(tmp_path / "nope.yaml"), *g]) == 2
    assert "Traceback" in capsys.readouterr().err


# =============================================================================
# init
# =============================================================================

@pytest.mark.parametrize("template", cli.TEMPLATES)
def test_init_every_template(tmp_path, template, capsys):
    assert main(["init", f"my-{template}", "--template", template, "--dir", str(tmp_path)]) == 0
    dest = tmp_path / f"my-{template}.yaml"
    pb = load_playbook(str(dest), env={})
    assert pb.name == f"my-{template}"
    assert "Created" in capsys.readouterr().out
    # comments are preserved
    assert dest.read_text(encoding="utf-8").lstrip().startswith("#")


def test_init_refuses_to_overwrite(tmp_path, capsys):
    assert main(["init", "acme", "--dir", str(tmp_path)]) == 0
    before = (tmp_path / "acme.yaml").read_text(encoding="utf-8")
    capsys.readouterr()
    assert main(["init", "acme", "--template", "recruitment", "--dir", str(tmp_path)]) == 1
    assert "already exists" in capsys.readouterr().err
    assert (tmp_path / "acme.yaml").read_text(encoding="utf-8") == before


def test_init_rejects_bad_names_and_accepts_yaml_suffix(tmp_path, capsys):
    assert main(["init", "bad name", "--dir", str(tmp_path)]) == 2
    assert "invalid playbook name" in capsys.readouterr().err
    assert main(["init", "client-x.yaml", "--dir", str(tmp_path)]) == 0
    assert (tmp_path / "client-x.yaml").exists() and not (tmp_path / "client-x.yaml.yaml").exists()


# =============================================================================
# validate
# =============================================================================

def test_validate_demo_offline_passes(g, capsys):
    assert main(["validate", "-p", DEMO, *g]) == 0
    out = capsys.readouterr().out
    assert "[FAIL]" not in out and "OK - ready to run" in out
    for label in ("source csv", "finder csv", "finder pattern", "verifier basic", "writer template",
                  "exporter instantly_csv", "exporter smartlead_csv", "notifier console"):
        assert label in out


def test_validate_broken_playbook_fails(tmp_path, g, capsys):
    pb = write_playbook(tmp_path, {
        "name": "broken",
        "mode": "outbound",   # the writer + hand-over exporters are only checked strictly in outbound mode
        "sources": [{"type": "nosuchsource"},
                    {"type": "csv", "path": "does/not/exist.csv"},
                    {"type": "theirstack"},
                    {"type": "adzuna"}],
        "enrichment": {"finders": [{"type": "apollo"}, {"type": "pattern"}],
                       "verifier": {"type": "millionverifier"}},
        "writer": {"type": "ai", "provider": "anthropic"},
        "outbound": {"exporters": [{"type": "instantly"}, {"type": "csv"}]},
        "notify": {"channels": [{"type": "slack"}]},
    })
    assert main(["validate", "-p", pb, *g]) == 1
    out = capsys.readouterr().out
    assert "unknown source type 'nosuchsource'" in out
    assert "file not found: does/not/exist.csv" in out
    assert "$THEIRSTACK_API_KEY" in out and "$ADZUNA_APP_ID" in out and "$ADZUNA_APP_KEY" in out
    assert "$APOLLO_API_KEY" in out and "$MILLIONVERIFIER_API_KEY" in out
    assert "$ANTHROPIC_API_KEY" in out and "template writer would be used instead" in out
    assert "missing 'campaign_id'" in out and "$INSTANTLY_API_KEY" in out
    assert "$SLACK_WEBHOOK_URL" in out
    assert "problem(s)" in out


def test_validate_with_keys_present_passes(tmp_path, capsys, monkeypatch):
    envf = tmp_path / "keys.env"
    envf.write_text("THEIRSTACK_API_KEY=ts\nAPOLLO_API_KEY=ap\nMILLIONVERIFIER_API_KEY=mv\n"
                    "ANTHROPIC_API_KEY=an\n", encoding="utf-8")
    pb = write_playbook(tmp_path, {
        "name": "keyed", "offer": {"sender_name": "Alex"}, "buyers": {"titles": ["CEO"]},
        "sources": [{"type": "theirstack", "job_titles": ["accountant"]}],
        "enrichment": {"finders": [{"type": "apollo"}], "verifier": {"type": "millionverifier"}},
        "writer": {"type": "ai", "provider": "anthropic", "model": "claude-opus-5"},
    })
    assert main(["validate", "-p", pb, "--db", str(tmp_path / "t.db"), "--env-file", str(envf)]) == 0
    out = capsys.readouterr().out
    assert "[FAIL]" not in out and "network" in out
    assert "--dry-run" in out  # a playbook that uses the network is rehearsed first


def test_validate_openai_compatible_needs_explicit_key(tmp_path, g, capsys, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    pb = write_playbook(tmp_path, {
        "name": "compat", "mode": "outbound", "sources": [{"type": "csv", "path": "examples/data/demo_signals.csv"}],
        "writer": {"type": "ai", "provider": "openai_compatible", "model": "llama",
                   "base_url": "https://openrouter.ai/api/v1"}})
    assert main(["validate", "-p", pb, *g]) == 1
    assert "api_key_env" in capsys.readouterr().out


def test_validate_dry_run_downgrades_missing_keys(tmp_path, g, capsys):
    pb = write_playbook(tmp_path, {"name": "net", "sources": [{"type": "theirstack"}],
                                   "enrichment": {"finders": [{"type": "hunter"}]}})
    assert main(["validate", "-p", pb, *g]) == 1
    capsys.readouterr()
    assert main(["validate", "-p", pb, "--dry-run", *g]) == 0
    out = capsys.readouterr().out
    assert "[warn] source theirstack" in out and "skipped in --dry-run" in out
    assert "leadgen run -p" in out and "--dry-run" in out


def test_validate_skips_disabled_and_flags_empty_sources(tmp_path, g, capsys):
    pb = write_playbook(tmp_path, {"name": "empty",
                                   "sources": [{"type": "theirstack", "enabled": False}]})
    assert main(["validate", "-p", pb, *g]) == 1
    out = capsys.readouterr().out
    assert "[skip] source theirstack" in out and "no sources configured" in out


def test_validate_every_shipped_playbook_runs(g, capsys):
    files = list(SHIPPED_PLAYBOOKS)
    assert len(files) >= 7
    for f in files:
        code = main(["validate", "-p", str(f.relative_to(REPO)), *g])
        out = capsys.readouterr().out
        assert code in (0, 1), f
        assert not re.search(r"unknown \w+ type", out), f  # every adapter type exists
        assert "cannot be created" not in out and "file not found" not in out, f


# =============================================================================
# run / leads / demo
# =============================================================================

def test_run_prints_summary_files_and_next_steps(tmp_path, g, capsys):
    assert run_demo(g, tmp_path) == 0
    out = capsys.readouterr().out
    assert "Running playbook 'demo-offline'" in out
    assert out.count("sourced companies") == 1          # console notifier shows it; not printed twice
    assert "opportunities.csv" in out and "rejected.csv" in out and "Next:" in out
    run_dir = latest_run_dir(tmp_path)
    for name in ("opportunities.csv", "leads.json", "instantly_upload.csv", "smartlead_upload.csv",
                 "rejected.csv", "summary.json"):
        assert (run_dir / name).exists(), name


def test_run_prints_summary_when_console_is_not_configured(tmp_path, g, capsys):
    data = yaml.safe_load((REPO / DEMO).read_text(encoding="utf-8"))
    data["notify"] = {"channels": [], "on": []}
    pb = write_playbook(tmp_path, data)
    assert main(["run", "-p", pb, "--out", str(tmp_path / "out"), *g]) == 0
    out = capsys.readouterr().out
    assert out.count("sourced companies") == 1 and "[RUN SUMMARY]" not in out


def test_run_exit_code_when_every_source_fails(tmp_path, g, capsys):
    pb = write_playbook(tmp_path, {"name": "nosrc", "sources": [{"type": "csv", "path": "nope.csv"}]})
    assert main(["run", "-p", pb, "--out", str(tmp_path / "out"), *g]) == 1
    captured = capsys.readouterr()
    assert "every source failed" in captured.err and "file not found" in captured.out


def test_run_one_of_two_unlabeled_sources_failing_is_not_every_source(tmp_path, g, capsys):
    """Both sources are reported as 'source csv: ...'; one failure must not count for both."""
    data = yaml.safe_load((REPO / DEMO).read_text(encoding="utf-8"))
    data["sources"] = [{"type": "csv", "path": str(REPO / "examples/data/demo_signals.csv")},
                       {"type": "csv", "path": "missing-demo.csv"}]
    data["enrichment"]["finders"][0]["path"] = str(REPO / "examples/data/demo_contacts.csv")
    pb = write_playbook(tmp_path, data)
    assert main(["run", "-p", pb, "--out", str(tmp_path / "out"), *g]) == 0
    captured = capsys.readouterr()
    assert "every source failed" not in captured.err and "file not found" in captured.out
    summary = json.loads((latest_run_dir(tmp_path) / "summary.json").read_text(encoding="utf-8"))
    assert summary["counts"]["sourced"] > 0 and summary["counts"]["exported"] > 0
    # ...while both failing is still an error
    data["sources"][0]["path"] = "nope.csv"
    pb = write_playbook(tmp_path, data)
    assert main(["run", "-p", pb, "--out", str(tmp_path / "out"), *g]) == 1
    assert "every source failed (2 of 2)" in capsys.readouterr().err


def test_failed_sources_counts_each_source_once():
    pb = cli.from_dict({"name": "x", "sources": [{"type": "csv", "path": "a.csv"}, {"type": "csv", "path": "b.csv"},
                                                 {"type": "json", "path": "c.json", "label": "crm"}]}, env={})
    assert cli._failed_sources(pb, ["source csv: file not found: b.csv"], False) == (3, 1)
    assert cli._failed_sources(pb, ["source csv: x", "source csv: y"], False) == (3, 2)
    assert cli._failed_sources(pb, ["source csv: x", "source csv: y", "source crm: z"], False) == (3, 3)
    assert cli._failed_sources(pb, ["enrich csv: x"], False) == (3, 0)


def test_run_limit_and_dry_run(tmp_path, g, capsys):
    assert main(["run", "-p", DEMO, "--out", str(tmp_path / "out"), "--limit", "3", "--dry-run", *g]) == 0
    out = capsys.readouterr().out
    assert "dry run" in out
    summary = json.loads((latest_run_dir(tmp_path) / "summary.json").read_text(encoding="utf-8"))
    assert summary["counts"]["exported"] == 0          # dry run never marks anything handed over
    assert len(summary["top"]) <= 3
    assert main(["run", "-p", DEMO, "--limit", "0", *g]) == 2


def test_dry_run_upload_files_are_rehearsals_not_ready_to_import(tmp_path, g, capsys):
    """A dry run records nobody as handed over, so its upload files must never look importable."""
    assert main(["run", "-p", DEMO, "--out", str(tmp_path / "dry"), "--dry-run", *g]) == 0
    out = capsys.readouterr().out
    run_dir = next((tmp_path / "dry").iterdir())
    assert not (run_dir / "instantly_upload.csv").exists() and not (run_dir / "smartlead_upload.csv").exists()
    assert read_rows(run_dir / "instantly_upload.dry-run.csv")               # still previewable
    assert (run_dir / "smartlead_upload.dry-run.csv").exists()
    assert "ready to import" not in out and "do NOT import" in out and "without --dry-run" in out
    assert main(["run", "-p", DEMO, "--out", str(tmp_path / "real"), *g]) == 0   # the real run
    out = capsys.readouterr().out
    run_dir = next((tmp_path / "real").iterdir())
    assert (run_dir / "instantly_upload.csv").exists() and "ready to import into Instantly" in out
    assert not list(run_dir.glob("*.dry-run.*")) and "do NOT import" not in out


def read_rows(path: Path) -> List[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_run_dry_run_with_only_network_sources_explains_empty_result(tmp_path, g, capsys):
    pb = write_playbook(tmp_path, {"name": "netonly", "sources": [{"type": "theirstack"}]})
    assert main(["run", "-p", pb, "--out", str(tmp_path / "out"), "--dry-run", *g]) == 0
    assert "every source uses the network" in capsys.readouterr().out


def test_leads_table_and_filters(tmp_path, g, capsys):
    assert main(["leads", "-p", DEMO, *g]) == 1          # no runs yet
    assert "no finished runs" in capsys.readouterr().err
    run_demo(g, tmp_path)
    capsys.readouterr()
    assert main(["leads", "-p", DEMO, *g]) == 0
    out = capsys.readouterr().out
    assert "Brightwave Analytics" in out and "score" in out and "top signal" in out
    assert main(["leads", "-p", DEMO, "--tier", "hot", "--limit", "2", *g]) == 0
    out = capsys.readouterr().out
    assert "showing 2 of" in out and " skip " not in out
    assert main(["leads", "--run", "all", *g]) == 0      # without a playbook: every playbook
    assert "all playbooks" in capsys.readouterr().out
    assert main(["leads", "-p", DEMO, "--run", "nope", *g]) == 1
    assert "not found" in capsys.readouterr().err


def test_demo_report(tmp_path, g, capsys):
    assert main(["demo", "-p", DEMO, *g]) == 1
    assert "no finished runs" in capsys.readouterr().err
    run_demo(g, tmp_path)
    capsys.readouterr()
    out_dir = tmp_path / "demo"
    assert main(["demo", "-p", DEMO, "--top", "3", "--prospect", "Acme Talent", "--out", str(out_dir), *g]) == 0
    out = capsys.readouterr().out
    md, page = out_dir / "demo-acme-talent.md", out_dir / "demo-acme-talent.html"
    assert str(md) in out and md.exists() and page.exists()
    text = md.read_text(encoding="utf-8")
    assert "Live opportunities for Acme Talent" in text and "Brightwave Analytics" in text
    assert "maya.okafor@brightwave-demo.com" not in text     # masked by default
    assert main(["demo", "-p", DEMO, "--no-mask", "--out", str(out_dir), *g]) == 0
    assert "maya.okafor@brightwave-demo.com" in (out_dir / "demo.md").read_text(encoding="utf-8")


def test_demo_default_out_dir_is_the_run_folder(tmp_path, g, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data = yaml.safe_load((REPO / DEMO).read_text(encoding="utf-8"))
    data["sources"][0]["path"] = str(REPO / "examples/data/demo_signals.csv")
    data["enrichment"]["finders"][0]["path"] = str(REPO / "examples/data/demo_contacts.csv")
    pb = write_playbook(tmp_path, data, "demo.yaml")
    assert main(["run", "-p", pb, *g]) == 0
    run_id = Store(g[1]).latest_run_id("demo-offline")
    capsys.readouterr()
    assert main(["demo", "-p", pb, *g]) == 0
    assert (tmp_path / "output" / "demo-offline" / run_id / "demo.html").exists()


# =============================================================================
# replies / stats / mark / suppress / followups / adapters
# =============================================================================

def test_replies_command(tmp_path, g, capsys):
    run_demo(g, tmp_path)
    capsys.readouterr()
    replies = tmp_path / "replies.csv"
    with open(replies, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["from", "subject", "body", "received_at"])
        w.writerow(["Maya Okafor <maya.okafor@brightwave-demo.com>", "Re: hi",
                    "Sounds good - send me some times for a call.", "2026-09-20T10:00:00Z"])
        w.writerow(["s.vermeer@harborline-demo.com", "Re: hi", "=HYPERLINK(\"x\") not interested, thanks",
                    "2026-09-20T11:00:00Z"])
    out_dir = tmp_path / "rep"
    assert main(["replies", "-p", DEMO, "--file", str(replies), "--out", str(out_dir), *g]) == 0
    out = capsys.readouterr().out
    assert "Classified 2 replies" in out and "positive" in out and "negative" in out
    rows = list(csv.DictReader(open(out_dir / "replies_classified.csv", encoding="utf-8")))
    assert [r["category"] for r in rows] == ["positive", "negative"]
    assert rows[0]["company"] == "Brightwave Analytics" and rows[0]["lead_id"]
    assert rows[1]["body"].startswith("'=")                       # formula-injection guard
    # re-importing the same file is safe: nothing is processed twice
    assert main(["replies", "-p", DEMO, "--file", str(replies), "--out", str(out_dir), *g]) == 0
    assert "duplicate" in capsys.readouterr().out
    assert main(["replies", "-p", DEMO, "--file", str(tmp_path / "nope.csv"), *g]) == 2
    empty = tmp_path / "empty.csv"
    empty.write_text("from,body\n", encoding="utf-8")
    assert main(["replies", "-p", DEMO, "--file", str(empty), *g]) == 1


def test_stats(tmp_path, g, capsys):
    assert main(["stats", *g]) == 0
    assert "No leads recorded yet" in capsys.readouterr().out
    run_demo(g, tmp_path)
    capsys.readouterr()
    assert main(["stats", "-p", DEMO, *g]) == 0
    out = capsys.readouterr().out
    assert "Funnel: demo-offline" in out and "exported" in out and "Recent runs" in out
    assert main(["stats", "-p", DEMO, "--since", "2020-01-01", *g]) == 0
    assert "since 2020-01-01" in capsys.readouterr().out
    assert main(["stats", "--since", "someday", *g]) == 2


def test_mark(tmp_path, g, capsys):
    run_demo(g, tmp_path)
    capsys.readouterr()
    assert main(["mark", "--email", "maya.okafor@brightwave-demo.com", "--stage", "booked", *g]) == 0
    assert "exported -> booked" in capsys.readouterr().out
    store = Store(g[1])
    lead = store.find_lead_by_email("maya.okafor@brightwave-demo.com")
    assert lead.stage == Stage.BOOKED
    # backwards needs --force
    assert main(["mark", "--lead", lead.id, "--stage", "replied", *g]) == 0
    assert "--force" in capsys.readouterr().out
    assert store.get_lead(lead.id).stage == Stage.BOOKED
    assert main(["mark", "--lead", lead.id, "--stage", "replied", "--force", "--note", "oops", *g]) == 0
    assert store.get_lead(lead.id).stage == Stage.REPLIED
    assert main(["mark", "--email", "nobody@nowhere-demo.com", "--stage", "won", *g]) == 1
    assert "no lead with email nobody@nowhere-demo.com found" in capsys.readouterr().err
    assert main(["mark", "--email", "x@y.com", "--stage", "nonsense", *g]) == 2
    assert main(["mark", "--stage", "won", *g]) == 2        # --email or --lead is required
    store.close()


def test_mark_exported_by_hand_is_never_handed_over_again(tmp_path, g, capsys):
    """Someone contacted by hand and marked 'exported' must count as handed over for dedupe."""
    data = yaml.safe_load((REPO / DEMO).read_text(encoding="utf-8"))
    data["outbound"]["exporters"] = [{"type": "csv"}]            # review only: nobody handed over
    review = write_playbook(tmp_path, data, "review.yaml")
    assert main(["run", "-p", review, "--out", str(tmp_path / "out"), *g]) == 0
    store = Store(g[1])
    lead = store.find_lead_by_email("maya.okafor@brightwave-demo.com")
    assert lead.stage == Stage.READY and not store.was_exported(lead.id)
    capsys.readouterr()
    assert main(["mark", "-p", review, "--email", "maya.okafor@brightwave-demo.com", "--stage", "exported",
                 *g]) == 0
    assert "ready -> exported" in capsys.readouterr().out
    assert store.get_lead(lead.id).stage == Stage.EXPORTED and store.was_exported(lead.id)
    assert store.recently_contacted("maya.okafor@brightwave-demo.com", 90, exclude_lead_id="other")
    # marking again keeps the original hand-over time
    first = store.conn.execute("SELECT exported_at FROM leads WHERE id=?", (lead.id,)).fetchone()[0]
    assert main(["mark", "--lead", lead.id, "--stage", "exported", *g]) == 0
    assert "already 'exported'" in capsys.readouterr().out
    assert store.conn.execute("SELECT exported_at FROM leads WHERE id=?", (lead.id,)).fetchone()[0] == first
    # the next run with a sending-tool export leaves Maya out
    data["outbound"]["exporters"] = [{"type": "instantly_csv"}]
    send = write_playbook(tmp_path, data, "send.yaml")
    assert main(["run", "-p", send, "--out", str(tmp_path / "out2"), *g]) == 0
    upload = next((tmp_path / "out2").iterdir()) / "instantly_upload.csv"
    emails = [r["email"] for r in csv.DictReader(open(upload, encoding="utf-8"))]
    assert emails and "maya.okafor@brightwave-demo.com" not in emails
    store.close()


def test_suppress(tmp_path, g, capsys):
    assert main(["suppress", "list", *g]) == 0
    assert "empty" in capsys.readouterr().out
    assert main(["suppress", "add", "Bad@Example-Demo.com", "--reason", "asked", *g]) == 0
    assert main(["suppress", "add", "https://www.competitor-demo.com", *g]) == 0
    txt = tmp_path / "list.txt"
    txt.write_text("# do not contact\nfoo@bar-demo.com\nbaz-demo.com  # a domain\n\n", encoding="utf-8")
    assert main(["suppress", "add", "--file", str(txt), *g]) == 0
    csvf = tmp_path / "list.csv"
    csvf.write_text("name,email\nJo,jo@corp-demo.com\n", encoding="utf-8")
    assert main(["suppress", "add", "--file", str(csvf), "--kind", "email", *g]) == 0
    capsys.readouterr()
    store = Store(g[1])
    assert store.is_suppressed(email="bad@example-demo.com")
    assert store.is_suppressed(domain="competitor-demo.com") and store.is_suppressed(email="x@baz-demo.com")
    assert store.is_suppressed(email="foo@bar-demo.com") and store.is_suppressed(email="jo@corp-demo.com")
    assert main(["suppress", "list", *g]) == 0
    out = capsys.readouterr().out
    assert "5 suppressed" in out and "asked" in out
    assert main(["suppress", "remove", "competitor-demo.com", *g]) == 0
    assert "Removed 1" in capsys.readouterr().out
    assert not store.is_suppressed(domain="competitor-demo.com")
    assert main(["suppress", "remove", "never-added-demo.com", *g]) == 0
    assert "Removed 0 value(s) (of 1 given)" in capsys.readouterr().out
    assert main(["suppress", "add", *g]) == 2
    store.close()


def test_suppress_remove_email_keeps_the_domain_block(g, capsys):
    """Removing one person must not lift a whole-domain 'never contact anyone at acme.com'."""
    assert main(["suppress", "add", "acme-demo.com", "--reason", "nobody at acme", *g]) == 0
    assert main(["suppress", "add", "jane@acme-demo.com", *g]) == 0
    capsys.readouterr()
    assert main(["suppress", "remove", "jane@acme-demo.com", *g]) == 0
    assert "Removed 1 value(s) from" in capsys.readouterr().out
    store = Store(g[1])
    assert [(r["value"], r["kind"]) for r in store.list_suppressed()] == [("acme-demo.com", "domain")]
    assert store.is_suppressed(email="bob@acme-demo.com")
    # --kind is honoured: removing the domain entry leaves an email entry alone
    assert main(["suppress", "add", "jane@acme-demo.com", *g]) == 0
    assert main(["suppress", "remove", "acme-demo.com", "--kind", "email", *g]) == 0
    assert store.is_suppressed(email="bob@acme-demo.com")
    assert main(["suppress", "remove", "acme-demo.com", *g]) == 0
    assert not store.is_suppressed(email="bob@acme-demo.com")
    assert store.is_suppressed(email="jane@acme-demo.com")
    store.close()


def test_suppress_add_understands_blocklist_notations(g, capsys):
    for v in ("@acme-demo.com", "*@beta-demo.io", "*.eps-demo.io", "Jane Doe <Jane@Gamma-demo.com>",
              "mailto:zed@delta-demo.io"):
        assert main(["suppress", "add", v, *g]) == 0, v
    store = Store(g[1])
    assert sorted((r["value"], r["kind"]) for r in store.list_suppressed()) == [
        ("acme-demo.com", "domain"), ("beta-demo.io", "domain"), ("eps-demo.io", "domain"),
        ("jane@gamma-demo.com", "email"), ("zed@delta-demo.io", "email")]
    for email in ("bob@acme-demo.com", "x@beta-demo.io", "a@eps-demo.io", "jane@gamma-demo.com",
                  "zed@delta-demo.io"):
        assert store.is_suppressed(email=email), email
    capsys.readouterr()
    # values that can never match are refused loudly instead of "Added 1 value(s)"
    assert main(["suppress", "add", "Jane Doe", *g]) == 1
    captured = capsys.readouterr()
    assert "Added 0 value(s) (of 1 given)" in captured.out and "skipped 'Jane Doe'" in captured.err
    assert main(["suppress", "add", "acme-demo.com", "--kind", "email", *g]) == 1
    assert len(store.list_suppressed()) == 5
    store.close()


@pytest.mark.parametrize("content", [
    "Name,Email Address,Status\nJane Doe,jane@acme-demo.com,unsubscribed\nBob Roe,bob@x-demo.com,unsubscribed\n",
    "First;Last;E-mail\nJane;Doe;jane@acme-demo.com\nBob;Roe;BOB@x-demo.com\n",
    "Mailing list,Work email\nnewsletter,jane@acme-demo.com\nnewsletter,bob@x-demo.com\n",
    "jane@acme-demo.com,Jane\nbob@x-demo.com,Bob\n",                       # no header row
])
def test_suppress_add_file_finds_the_email_column(tmp_path, g, capsys, content):
    f = tmp_path / "unsubscribes.csv"
    f.write_text(content, encoding="utf-8")
    assert main(["suppress", "add", "--file", str(f), *g]) == 0
    assert "Added 2 value(s) to" in capsys.readouterr().out
    store = Store(g[1])
    assert sorted(r["value"] for r in store.list_suppressed()) == ["bob@x-demo.com", "jane@acme-demo.com"]
    assert all(r["kind"] == "email" for r in store.list_suppressed())
    store.close()


def test_suppress_add_file_website_column(tmp_path, g, capsys):
    f = tmp_path / "competitors.csv"
    f.write_text("Company,Website\nAcme,https://www.acme-demo.com/about\n", encoding="utf-8")
    assert main(["suppress", "add", "--file", str(f), *g]) == 0
    store = Store(g[1])
    assert [(r["value"], r["kind"]) for r in store.list_suppressed()] == [("acme-demo.com", "domain")]
    store.close()


def test_followups(tmp_path, g, capsys):
    # followups is an outbound-mode command: without -p the default mode (delivery) refuses it
    assert main(["followups", "-p", DEMO, *g]) == 0
    assert "No follow-ups due" in capsys.readouterr().out
    run_demo(g, tmp_path)
    replies = tmp_path / "r.csv"
    replies.write_text("from,body,received_at\ngraham.holt@redfern-demo.com,"
                       "\"Not right now - try me again next quarter.\",2026-09-20T09:00:00Z\n", encoding="utf-8")
    assert main(["replies", "-p", DEMO, "--file", str(replies), "--out", str(tmp_path / "rep"), *g]) == 0
    capsys.readouterr()
    assert main(["followups", "-p", DEMO, *g]) == 0          # due in the future, not today
    assert "No follow-ups due today" in capsys.readouterr().out
    assert main(["followups", "-p", DEMO, "--days", "120", *g]) == 0
    out = capsys.readouterr().out
    assert "graham.holt@redfern-demo.com" in out and "Redfern Manufacturing" in out and "timing" in out
    store = Store(g[1])
    fid = store.conn.execute("SELECT id FROM followups").fetchone()["id"]
    assert main(["followups", "-p", DEMO, "--done", str(fid), *g]) == 0
    assert "marked done" in capsys.readouterr().out
    assert main(["followups", "-p", DEMO, "--days", "120", *g]) == 0
    assert "No follow-ups due" in capsys.readouterr().out
    assert main(["followups", "-p", DEMO, "--done", "9999", *g]) == 1
    store.close()


def test_adapters_lists_every_kind(g, capsys):
    assert main(["adapters", *g]) == 0
    out = capsys.readouterr().out
    for kind in ("source:", "finder:", "verifier:", "llm:", "writer:", "exporter:", "notifier:"):
        assert kind in out
    for name in ("theirstack", "millionverifier", "instantly_csv", "slack", "anthropic"):
        assert name in out
    assert "ADZUNA_APP_ID, ADZUNA_APP_KEY" in out and "APOLLO_API_KEY" in out


def test_python_dash_m_entry_point(tmp_path):
    import subprocess
    import sys

    proc = subprocess.run([sys.executable, "-m", "leadgen", "--version"], cwd=str(REPO),
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0 and "leadgen" in proc.stdout


def test_verbose_logging_goes_to_stderr(tmp_path, g, capsys):
    assert run_demo(g, tmp_path, "-v") == 0
    captured = capsys.readouterr()
    assert "info: source demo-signals" in captured.err
    assert "info:" not in captured.out


def test_format_table_alignment_and_clipping():
    text = cli.format_table(["a", "long"], [[1, "x" * 50], [22, "y"]], widths={"long": 10}, right=("a",))
    lines = text.splitlines()
    assert lines[0].startswith(" a  long") and lines[2].startswith(" 1  xxxxxxxxx…")
    assert lines[3] == "22  y"


PLUGIN_SOURCE = """
from leadgen import registry
from leadgen.sources.base import Source


class ListSource(Source):
    \"\"\"Companies typed straight into the playbook.\"\"\"

    name = "inline_list"
    offline = True

    def fetch(self):
        return []


registry.register("source", "inline_list", "my_leadgen_plugin:ListSource")
"""


def test_plugins_register_custom_adapters(tmp_path, monkeypatch, capsys):
    from leadgen import registry

    (tmp_path / "my_leadgen_plugin.py").write_text(PLUGIN_SOURCE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    envf = tmp_path / "p.env"
    envf.write_text("LEADGEN_PLUGINS=my_leadgen_plugin\n", encoding="utf-8")
    try:
        assert main(["adapters", "--env-file", str(envf)]) == 0
        assert "inline_list" in capsys.readouterr().out
        pb = write_playbook(tmp_path, {"name": "plug", "sources": [{"type": "inline_list"}]})
        assert main(["validate", "-p", pb, "--db", str(tmp_path / "t.db"), "--env-file", str(envf)]) == 0
        assert "[ok]   source inline_list" in capsys.readouterr().out
    finally:
        registry._REGISTRY["source"].pop("inline_list", None)
    envf.write_text("LEADGEN_PLUGINS=no_such_plugin_module\n", encoding="utf-8")
    assert main(["adapters", "--env-file", str(envf)]) == 2
    assert "cannot import plugin 'no_such_plugin_module'" in capsys.readouterr().err


def test_main_restores_logging_state(g, capsys):
    import logging

    logger = logging.getLogger("leadgen")
    level, handlers, propagate = logger.level, list(logger.handlers), logger.propagate
    assert main(["adapters", "-vv", *g]) == 0
    assert logger.level == level and logger.handlers == handlers and logger.propagate == propagate


def test_broken_pipe_is_quiet(monkeypatch, g, capsys):
    def closed(args):
        raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr(cli, "cmd_adapters", closed)
    assert main(["adapters", *g]) == 0
    assert capsys.readouterr().err == ""


def test_unopenable_database_is_friendly(tmp_path, env_file, capsys):
    (tmp_path / "adir").mkdir()
    assert main(["stats", "--db", str(tmp_path / "adir"), "--env-file", env_file]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: cannot open the database") and "Traceback" not in err


def test_replies_one_failure_does_not_stop_the_batch(tmp_path, g, capsys, monkeypatch):
    from leadgen import replies as replies_mod

    real = replies_mod.handle_reply

    def flaky(reply, ctx):
        if "boom" in reply.body:
            raise RuntimeError("cannot parse")
        return real(reply, ctx)

    monkeypatch.setattr(replies_mod, "handle_reply", flaky)
    f = tmp_path / "r.csv"
    f.write_text("from,body\na@one-demo.com,boom\nb@two-demo.com,Please unsubscribe me.\n", encoding="utf-8")
    assert main(["replies", "-p", DEMO, "--file", str(f), "--out", str(tmp_path / "o"), *g]) == 1
    captured = capsys.readouterr()
    assert "1 of 2 replies could not be processed" in captured.err
    rows = list(csv.DictReader(open(tmp_path / "o" / "replies_classified.csv", encoding="utf-8")))
    assert rows[0]["action"] == "error: cannot parse" and rows[1]["category"] == "unsubscribe"


def test_validate_accepts_file_alias_for_csv_source(tmp_path, g, capsys):
    pb = write_playbook(tmp_path, {"name": "alias", "buyers": {"titles": ["CEO"]}, "offer": {"sender_name": "A"},
                                   "sources": [{"type": "csv", "file": "examples/data/demo_signals.csv"}]})
    assert main(["validate", "-p", pb, *g]) == 0
    assert "[FAIL]" not in capsys.readouterr().out
