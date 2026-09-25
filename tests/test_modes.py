"""Delivery mode (the default) never writes, sends, handles replies or serves webhooks."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from leadgen import registry
from leadgen.modes import OutboundOnlyError, is_outbound, require_outbound
from leadgen.models import Reply
from leadgen.outbound.base import Exporter, ExportResult
from leadgen.pipeline import Pipeline, PipelineHooks
from leadgen.playbook import from_dict
from leadgen.usage import BudgetExceeded, UsageMeter

CALLS = {"send": 0, "review": 0}


class SpySender(Exporter):
    name = "spy_sender"
    scope = "outbound"
    is_send = True
    offline = True

    def export(self, leads, out_dir):  # pragma: no cover - must never run in delivery mode
        CALLS["send"] += 1
        return ExportResult(exporter=self.name, count=len(leads), exported_ids=[ld.id for ld in leads])


class SpyReview(Exporter):
    name = "spy_review"
    scope = "all"
    offline = True

    def export(self, leads, out_dir):
        CALLS["review"] += 1
        return ExportResult(exporter=self.name, count=len(leads))


registry.register("exporter", "spy_sender", "tests.test_modes:SpySender")
registry.register("exporter", "spy_review", "tests.test_modes:SpyReview")


def _pb(tmp_path: Path, mode: str):
    src = tmp_path / "jobs.csv"
    with open(src, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Company", "Domain", "Employees", "Job Title", "Date Posted", "First Name", "Last Name",
                    "Title", "Email", "Email Status"])
        w.writerow(["Acme", "acme-m.com", 100, "Accountant", "1 day ago", "Jane", "Doe", "CFO",
                    "jane@acme-m.com", "verified"])
    return {"mode": mode, "sources": [{"type": "csv", "path": str(src)}],
            "buyers": {"titles": ["CFO"]}, "scoring": {"tiers": {"hot": 40, "normal": 10}},
            "writer": {"type": "template"}, "notify": {"channels": []},
            "outbound": {"exporters": [{"type": "spy_sender"}, {"type": "spy_review"}, {"type": "instantly"}]}}


def test_engine_default_mode_is_delivery():
    assert from_dict({"name": "x"}).mode == "delivery"
    assert not is_outbound(from_dict({"name": "x"}))
    with pytest.raises(OutboundOnlyError, match="mode: outbound"):
        require_outbound(from_dict({"name": "x"}), "Reply handling")


def test_delivery_pipeline_never_writes_or_hands_over(make_ctx, tmp_path, monkeypatch):
    import leadgen.pipeline as pl

    def boom(*a, **k):  # the writer must not even be built
        raise AssertionError("build_writer called in delivery mode")
    monkeypatch.setattr(pl, "build_writer", boom)
    CALLS.update(send=0, review=0)
    ctx = make_ctx(**_pb(tmp_path, "delivery"))
    res = Pipeline(ctx, out_dir=tmp_path / "out").run()
    assert CALLS == {"send": 0, "review": 1}
    assert res.counts["written"] == 0 and res.counts["exported"] == 0
    assert all(not ld.messages for ld in res.leads)
    assert not any(ctx.store.was_exported(ld.id) for ld in res.leads)
    assert any("instantly" in w for w in res.warnings) and any("spy_sender" in w for w in res.warnings)
    assert "delivery mode" in res.summary()
    assert not ctx.http.calls  # the instantly exporter never talked to the network


def test_outbound_pipeline_still_writes_and_hands_over(make_ctx, tmp_path):
    CALLS.update(send=0, review=0)
    pb = _pb(tmp_path, "outbound")
    pb["outbound"]["exporters"] = [{"type": "spy_sender"}, {"type": "spy_review"}]
    ctx = make_ctx(**pb)
    res = Pipeline(ctx, out_dir=tmp_path / "out").run()
    assert CALLS == {"send": 1, "review": 1} and res.counts["written"] == 1 and res.counts["exported"] == 1


def test_delivery_mode_refuses_replies_and_server(make_ctx):
    from leadgen.replies import handle_reply
    from leadgen.server import make_server
    ctx = make_ctx(mode="delivery")
    with pytest.raises(OutboundOnlyError):
        handle_reply(Reply(from_email="a@b.com", body="yes please"), ctx)
    with pytest.raises(OutboundOnlyError):
        make_server(ctx, port=0)
    assert ctx.store.list_replies() == [] and ctx.store.due_followups(ctx.today) == []


def test_hooks_drop_companies_before_enrichment(make_ctx, tmp_path):
    class DropAll(PipelineHooks):
        def filter_company(self, company):
            return "already delivered"
    ctx = make_ctx(**_pb(tmp_path, "delivery"))
    res = Pipeline(ctx, out_dir=tmp_path / "out", hooks=DropAll()).run()
    assert res.leads == [] and res.counts["hook_rejected"] == 1
    assert res.rejected[-1]["stage"] == "delivery"


def test_budget_blocks_paid_requests_before_the_network(make_ctx):
    from tests.fakes import FakeHttp
    meter = UsageMeter(max_paid_lookups=1)
    meter.before_request("verifier", "millionverifier", True)
    with pytest.raises(BudgetExceeded):
        meter.before_request("verifier", "millionverifier", True)
    meter.before_request("source", "greenhouse", False)  # free requests are never blocked
    assert meter.paid_lookups == 1 and meter.exhausted
    ctx = make_ctx(env={"MILLIONVERIFIER_API_KEY": "k"})
    ctx.usage = UsageMeter(max_paid_lookups=0)
    ctx.http = FakeHttp()
    ctx.http.add("GET", "https://api.millionverifier.com", json={"result": "ok", "credits": 99})
    v = registry.create("verifier", {"type": "millionverifier"}, ctx)
    v.verify("jane@acme.com")
    assert ctx.usage.paid_lookups == 1
    ctx.usage.max_paid_lookups = 1
    with pytest.raises(BudgetExceeded):
        v.verify("bob@acme.com")
    assert len(ctx.http.calls) == 1
